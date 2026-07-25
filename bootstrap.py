#!/usr/bin/env python3
"""
Bootstrap script for beads-based orchestration.

Creates:
- .beads/ directory with beads CLI
- .claude/agents/ with code-reviewer and merge-supervisor
- .claude/hooks/ with enforcement hooks (Node.js)
- .claude/rules/ with beads-workflow and optional dev rules
- .claude/skills/ with project-discovery
- .claude/settings.json with hook configuration
- .claude/.manifest.json with file hashes for safe upgrades
- .claude/.upgrades/ with new versions of user-modified files
- CLAUDE.md with orchestrator instructions

Usage:
    python bootstrap.py [--project-name NAME] [--project-dir DIR] [--with-rules] [--force]
"""

import os
import sys
import json
import difflib
import hashlib
import re
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

try:
    import yaml as _yaml  # optional, used for omp/pi config.yml
except ImportError:
    _yaml = None

try:
    from adapters import (
        CLAUDE as _CLAUDE,
        CODEX as _CODEX,
        OPENCODE as _OPENCODE,
        PI as _PI,
        OMP as _OMP,
        OMO as _OMO,
        ALL_ADAPTERS as _ALL_ADAPTERS,
        HarnessAdapter as _HarnessAdapter,
        resolve as _resolve_harnesses,
        validate as _validate_harness,
    )
except ImportError:  # pragma: no cover - adapters.py must ship with bootstrap.py
    _ALL_ADAPTERS = ()
    _HarnessAdapter = None
    _resolve_harnesses = None
    _validate_harness = None

_SHELL = sys.platform == "win32"
SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = SCRIPT_DIR / "templates"

# Subprocess timeouts (seconds). Centralised so tests can patch them and
# avoid the "magic-number sprawl" smell (5/10/15/180 were sprinkled across
# subprocess.run calls before this constant existed).
_BD_TIMEOUT_SHORT = 5    # capability probes (bd --help, bd --version)
_BD_TIMEOUT_DEFAULT = 15  # one-shot subcommands (bd init, bd doctor, sync)
_BD_TIMEOUT_LONG = 180    # bd daemon / dolt startup — may be slow on first run
_BD_OUTPUT_GRACE = 2      # seconds to wait for proc.communicate() after kill
_GIT_TIMEOUT = 10         # git config / check-ignore fast-enough locally

# SHA-256 of every shipped .cjs hook template. Bump these in lockstep with any
# edit to templates/hooks/*.cjs. Used by copy_hooks() to fail loudly when a
# template was tampered with (Supply-Chain hardening, F-04 in the security
# audit): if any of the shipped hooks hash differently, refuse to install and
# print the offending path. The user can re-bootstrap with
# --allow-untouched-hooks to bypass (e.g. when developing a hook locally).
_EXPECTED_HOOK_HASHES: dict = {
    "bash-guard.cjs":                    "6278b654616962d18ab70f17f85969ba6363fb7bf37e652331ac8519e1267a00",
    "enforce-branch-before-edit.cjs":    "0cdae545a8487cd1e71830569072561ccbc39d1062abc554fa0cd5f39f346e78",
    "hook-utils.cjs":                    "5d8a3ad1d54a67ca4cc0db2d02e022e904cd59e485e1e12ba86ae893875eb99d",
    "nudge-claude-md-update.cjs":        "7d29dba4809b13b1b01c7e1062d6a138dcc6007c2dc4ae8c4c0fc96cf885c167",
    "session-start.cjs":                 "fd389759a2f402b8c7a753cddd7401b4d9480b65b5cb885daed8bfc94aa64402",
    "validate-completion.cjs":           "808263542c73072d480b15c4507d974b9e51e157456bf5d5aa9962a546e2ec87",
}

# Make sure the sibling `adapters.py` module is importable whether this file
# is executed as a script (`python bootstrap.py …`) or imported as a module
# (the pytest path). Inserting at index 0 keeps the bundled Python paths
# in the list too.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


# ---------------------------------------------------------------------------
# ANSI color — auto-enabled on a TTY, suppressed under NO_COLOR / TERM=dumb /
# when piped. configure_color() runs once from main(); library callers and
# tests get color OFF by default, so captured output stays byte-for-byte plain.
# ---------------------------------------------------------------------------
_ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m",
}
_COLOR_ENABLED = False


def configure_color(mode: str = "auto") -> None:
    """Set global color state. mode: 'always' | 'never' | 'auto'."""
    global _COLOR_ENABLED
    if mode == "always":
        _COLOR_ENABLED = True
    elif mode == "never":
        _COLOR_ENABLED = False
    else:
        _COLOR_ENABLED = (
            sys.stdout.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM") != "dumb"
        )


def _paint(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles when color is enabled; otherwise return as-is."""
    if not _COLOR_ENABLED or not text or not styles:
        return text
    codes = "".join(_ANSI[s] for s in styles)
    return f"{codes}{text}{_ANSI['reset']}"


def _color_diff_line(line: str) -> str:
    """Colorize one unified-diff line (no-op when color disabled)."""
    if line[:3] in ("---", "+++"):
        return _paint(line, "bold")
    if line.startswith("@@"):
        return _paint(line, "cyan")
    if line.startswith("+"):
        return _paint(line, "green")
    if line.startswith("-"):
        return _paint(line, "red")
    return line


# ============================================================================
# OBSOLETE ITEMS (per-release cleanup targets)
# ============================================================================
# v3.3.0 removes the memory-capture / recall.cjs knowledge-base system.
# Pre-manifest installs have these paths on disk but no manifest entry;
# _auto_inject_legacy_files retro-registers them before _cleanup_file runs.

# File paths relative to project_dir. Removed by cleanup_obsolete() ONLY IF
# the path is a key in manifest["files"] (i.e. we installed it — never touch
# user-created files). Backed up before deletion.
OBSOLETE_FILES: list[str] = [
    ".claude/hooks/memory-capture.cjs",
    ".claude/hooks/recall.cjs",
    ".beads/memory/recall.cjs",
]

# Directory paths relative to project_dir. Removed if they exist (no manifest
# check — directories aren't tracked individually). Always backed up before
# deletion. NOTE: .beads/memory is skipped if a non-empty knowledge.jsonl is
# still present — user data is preserved, warning printed.
OBSOLETE_DIRS: list[str] = [
    ".beads/memory",
]

# Substrings matched against hook command strings in .claude/settings.json.
# Any hook entry whose "hooks[0].command" contains one of these substrings
# is stripped. Original settings.json is backed up before writing.
OBSOLETE_SETTINGS_HOOKS: list[str] = [
    "memory-capture.cjs",
]

# Substrings matched against hook command strings in
# .claude/settings.local.json. Same semantics as OBSOLETE_SETTINGS_HOOKS.
# `bd prime` used to be a SessionStart hook there; the templated global
# settings.json now owns session bootstrapping, so legacy local entries go.
OBSOLETE_LOCAL_SETTINGS_PATTERNS: list[str] = [
    "bd prime",
]


# ============================================================================
# PROJECT NAME INFERENCE
# ============================================================================

def infer_project_name(project_dir: Path) -> str:
    """Auto-infer project name from package files or directory name."""
    for detect_fn in [_from_package_json, _from_pyproject, _from_cargo, _from_go_mod]:
        name = detect_fn(project_dir)
        if name:
            return name
    return project_dir.name.replace("-", " ").replace("_", " ").title()


def _from_package_json(project_dir: Path) -> str | None:
    p = project_dir / "package.json"
    if not p.exists():
        return None
    try:
        name = json.loads(p.read_text(encoding='utf-8')).get("name")
        return name.replace("-", " ").replace("_", " ").title() if name else None
    except Exception:
        return None


def _from_pyproject(project_dir: Path) -> str | None:
    if not tomllib:
        return None
    p = project_dir / "pyproject.toml"
    if not p.exists():
        return None
    try:
        data = tomllib.loads(p.read_text(encoding='utf-8'))
        name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
        return name.replace("-", " ").replace("_", " ").title() if name else None
    except Exception:
        return None


def _from_cargo(project_dir: Path) -> str | None:
    if not tomllib:
        return None
    p = project_dir / "Cargo.toml"
    if not p.exists():
        return None
    try:
        name = tomllib.loads(p.read_text(encoding='utf-8')).get("package", {}).get("name")
        return name.replace("-", " ").replace("_", " ").title() if name else None
    except Exception:
        return None


def _from_go_mod(project_dir: Path) -> str | None:
    p = project_dir / "go.mod"
    if not p.exists():
        return None
    try:
        for line in p.read_text(encoding='utf-8').splitlines():
            if line.startswith("module "):
                name = line.split()[1].split("/")[-1]
                return name.replace("-", " ").replace("_", " ").title()
    except Exception:
        pass
    return None


# ============================================================================
# HELPERS
# ============================================================================

def _render(source: Path, replacements: dict) -> str:
    content = source.read_text(encoding='utf-8')
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def copy_and_replace(source: Path, dest: Path, replacements: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render(source, replacements), encoding='utf-8')


# ============================================================================
# MANIFEST (upgrade tracking)
# ============================================================================

def bytes_sha256(data: bytes) -> str:
    """Return hex SHA-256 digest of raw bytes."""
    h = hashlib.sha256()
    h.update(data)
    return f"sha256:{h.hexdigest()}"


def file_sha256(path: Path) -> str:
    """Return hex SHA-256 digest of a file's contents."""
    return bytes_sha256(path.read_bytes())


def content_sha256(content: str) -> str:
    """Return hex SHA-256 digest of string content."""
    return bytes_sha256(content.encode("utf-8"))


def load_manifest(project_dir: Path) -> dict:
    """Load .claude/.manifest.json or return empty structure."""
    manifest_path = project_dir / ".claude" / ".manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": None, "installed_at": None, "files": {}}


def save_manifest(project_dir: Path, manifest: dict) -> None:
    """Write .claude/.manifest.json."""
    manifest_path = project_dir / ".claude" / ".manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def should_update_file(
    file_path: Path, relative_key: str, manifest: dict, force: bool
) -> tuple:
    """Decide whether to overwrite a file.

    Returns (should_update: bool, reason: str) where reason is one of:
    "new", "unchanged", "modified", "forced", "no_manifest".
    """
    if force:
        return True, "forced"
    if not file_path.exists():
        return True, "new"
    current_hash = file_sha256(file_path)
    recorded_hash = manifest.get("files", {}).get(relative_key)
    if recorded_hash is None:
        # Legacy install — treat as user-modified (safe default)
        return False, "no_manifest"
    if current_hash == recorded_hash:
        return True, "unchanged"
    return False, "modified"


def save_upgrade(project_dir: Path, relative_path: str, content: str) -> None:
    """Save new version of a user-modified file to .claude/.upgrades/."""
    dest = project_dir / ".claude" / ".upgrades" / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


class ChangeRecorder:
    """Guarded writes: byte-exact backup, atomic replace, per-file diff.

    Every overwriting write goes through put_file so nothing is replaced
    without a recoverable backup under .claude/.upgrades/<ts>/overwritten/
    and a recorded diff for the report. Owns the manifest and change list.
    """

    def __init__(self, project_dir, manifest=None, *,
                 force=False, dry_run=False, no_diff=False, timestamp=None):
        self.project_dir = Path(project_dir).resolve()
        self.manifest = manifest if manifest is not None else {"files": {}}
        self.manifest.setdefault("files", {})
        self.force = force
        self.dry_run = dry_run
        self.no_diff = no_diff
        self.timestamp = timestamp or _upgrade_timestamp()
        self.changes = []
        self._backup_created = False

    @property
    def backup_root(self):
        return self.project_dir / ".claude" / ".upgrades" / self.timestamp

    def _ensure_backup_dir(self):
        root = self.backup_root / "overwritten"
        if not self._backup_created:
            root.mkdir(parents=True, exist_ok=True)
            self._backup_created = True
        return root

    def _label(self, key, old_bytes):
        recorded = self.manifest.get("files", {}).get(key)
        if recorded is None:
            return "unmanaged"
        return "pristine" if bytes_sha256(old_bytes) == recorded else "locally-modified"

    @staticmethod
    def _diff_lines(old_bytes, new_bytes, key):
        old = old_bytes.decode("utf-8", errors="replace").splitlines()
        new = (new_bytes or b"").decode("utf-8", errors="replace").splitlines()
        return list(difflib.unified_diff(old, new, fromfile=key, tofile=key, lineterm=""))

    @staticmethod
    def _counts(diff):
        added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        return added, removed

    def _atomic_write(self, dest, data):
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".cp-tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _do_backup(self, dest, old_bytes):
        dest = Path(dest)
        # resolve() normalises macOS /var -> /private/var for the common case;
        # absolute() handles a managed file that is a symlink pointing OUTSIDE
        # the project (resolve() would escape project_dir and raise ValueError).
        rel = None
        for candidate in (dest.resolve(), dest.absolute()):
            try:
                rel = candidate.relative_to(self.project_dir)
                break
            except ValueError:
                continue
        if rel is None:
            rel = Path(dest.name)  # last resort: flat name, never crash
        path = self._ensure_backup_dir() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(old_bytes)
        return path

    def _record_tree_file(self, dest, key, old_b, new_b):
        if old_b == new_b:
            return
        if old_b is None:
            self.changes.append({"key": key, "action": "new", "label": None,
                                 "added": 0, "removed": 0, "diff": [], "backup": None})
            return
        action = "removed" if new_b is None else "overwritten"
        diff = self._diff_lines(old_b, new_b, key)
        added, removed = self._counts(diff)
        backup_path = self._do_backup(dest, old_b) if not self.dry_run else None
        self.changes.append({"key": key, "action": action,
                             "label": self._label(key, old_b),
                             "added": added, "removed": removed,
                             "diff": diff, "backup": backup_path})

    def _record_tree(self, dest_dir, src_dir, key_prefix):
        old_files = ({p.relative_to(dest_dir): p for p in dest_dir.rglob("*") if p.is_file()}
                     if dest_dir.exists() else {})
        new_files = {p.relative_to(src_dir): p for p in src_dir.rglob("*") if p.is_file()}
        for sub in sorted(set(old_files) | set(new_files), key=lambda p: p.as_posix()):
            key = f"{key_prefix}/{sub.as_posix()}"
            old_b = old_files[sub].read_bytes() if sub in old_files else None
            new_b = new_files[sub].read_bytes() if sub in new_files else None
            self._record_tree_file(dest_dir / sub, key, old_b, new_b)

    def replace_tree(self, dest_dir, src_dir, key_prefix):
        """Back up + record per-file diffs, then byte-exact replace dest_dir with src_dir."""
        dest_dir, src_dir = Path(dest_dir), Path(src_dir)
        self._record_tree(dest_dir, src_dir, key_prefix)
        if self.dry_run:
            return
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(src_dir, dest_dir)
        prefix = key_prefix.rstrip("/") + "/"
        for k in [k for k in self.manifest["files"] if k.startswith(prefix)]:
            self.manifest["files"].pop(k)
        for f in dest_dir.rglob("*"):
            if f.is_file():
                key = str(f.resolve().relative_to(self.project_dir / ".claude")).replace("\\", "/")
                self.manifest["files"][key] = file_sha256(f)

    @staticmethod
    def _classify(old_bytes, new_bytes, backup):
        if old_bytes is None:
            return "new"
        return "overwritten" if backup else "appended"

    def put_file(self, dest, new_bytes, key, *, backup=True):
        """Write new_bytes to dest with backup (if overwriting) + recorded diff.

        backup=True  -> managed file: back up old content, track in manifest.
        backup=False -> append/unmanaged file: no backup, no manifest entry.
        Returns "new" | "unchanged" | "overwritten" | "appended".
        """
        dest = Path(dest)
        old_bytes = dest.read_bytes() if dest.exists() else None
        if old_bytes == new_bytes:
            return "unchanged"
        action = self._classify(old_bytes, new_bytes, backup)
        diff = self._diff_lines(old_bytes, new_bytes, key) if old_bytes is not None else []
        added, removed = self._counts(diff)
        label = self._label(key, old_bytes) if old_bytes is not None else None
        backup_path = None
        if old_bytes is not None and backup and not self.dry_run:
            backup_path = self._do_backup(dest, old_bytes)
        # Record the change BEFORE the atomic write so a write failure
        # still leaves an audit trail in print_report — the backup on
        # disk is otherwise invisible to the user.
        self.changes.append({"key": key, "action": action, "label": label,
                             "added": added, "removed": removed,
                             "diff": diff, "backup": backup_path})
        if not self.dry_run:
            self._atomic_write(dest, new_bytes)
            if backup:
                self.manifest["files"][key] = bytes_sha256(new_bytes)
        return action

    def record_skip(self, key):
        """Record a user-modified file that was NOT written (report-only)."""
        self.changes.append({"key": key, "action": "kept", "label": "locally-modified",
                             "added": 0, "removed": 0, "diff": [], "backup": None})

    _VERB = {"overwritten": "UPDATE", "new": "NEW", "appended": "APPEND", "removed": "REMOVE"}
    _VERB_STYLE = {"overwritten": ("yellow",), "new": ("green",),
                   "appended": ("cyan",), "removed": ("red",)}

    @staticmethod
    def _report_line(c, width):
        raw = ChangeRecorder._VERB.get(c["action"], c["action"].upper())
        verb = _paint(f"{raw:<7}", *ChangeRecorder._VERB_STYLE.get(c["action"], ()))
        key = f"{c['key']:<{width}}"
        text = c.get("note") or (
            "your edits will be replaced" if c.get("label") == "locally-modified" else "")
        note = f"   {_paint(text, 'dim')}" if text else ""
        counts = "" if c["action"] == "new" else (
            f"   {_paint('+' + str(c['added']), 'green')} "
            f"{_paint('-' + str(c['removed']), 'red')}")
        return f"  {verb} {key}{note}{counts}".rstrip()

    def _headline(self, n):
        plural = "s" if n != 1 else ""
        if self.dry_run:
            msg = (f"DRY-RUN — {n} file{plural} would change, nothing written"
                   if n else "DRY-RUN — no changes, everything up to date")
            return _paint(msg, "bold")
        if not n:
            return _paint("No changes — everything up to date", "bold")
        backup = f"   backup: {self.backup_root}" if self._backup_created else ""
        return _paint(f"{n} file{plural} changed", "bold") + _paint(backup, "dim")

    def _print_diffs(self, changed):
        for c in changed:
            if c["diff"]:
                print("")
                for line in c["diff"]:
                    print(_color_diff_line(line))

    def print_report(self):
        changed = [c for c in self.changes if c["action"] not in ("unchanged", "kept")]
        kept = [c for c in self.changes if c["action"] == "kept"]
        print(f"\n{self._headline(len(changed))}")
        if changed:
            width = max(len(c["key"]) for c in changed)
            print("")
            for c in changed:
                print(self._report_line(c, width))
        if kept:
            print("\n" + _paint(
                "  KEPT (you modified these — new version staged in .claude/.upgrades/):",
                "dim"))
            for c in kept:
                print(f"    {c['key']}")
        if changed:
            if self.dry_run:
                print("\n  Run without --dry-run to apply · --no-diff hides diffs")
            if not self.no_diff:
                print("\n  ── diffs ──")
                self._print_diffs(changed)


# ============================================================================
# CHANGE SUMMARIZATION
# ============================================================================

_SUMMARY_VERB = {"new": "new", "overwritten": "updated", "appended": "appended",
                 "removed": "removed", "kept": "kept"}
# Display order, keyed by RAW action (same vocabulary as _SUMMARY_VERB keys).
_SUMMARY_ORDER = ["new", "overwritten", "appended", "removed", "kept"]


def summarize_changes(changes_slice) -> str:
    """One-line step summary from a slice of recorder.changes (verb-counted)."""
    tally = {}
    for c in changes_slice:
        tally[c["action"]] = tally.get(c["action"], 0) + 1
    if not tally:
        return "no changes"
    return " · ".join(f"{tally[a]} {_SUMMARY_VERB.get(a, a)}"
                      for a in _SUMMARY_ORDER if a in tally)


# ============================================================================
# UPGRADE CLEANUP
# ============================================================================

def _upgrade_timestamp() -> str:
    """YYYYMMDDTHHMMSSZ — one folder per cleanup_obsolete call."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hook_command_matches(hook_entry: dict, patterns: list) -> tuple:
    """Return (command_str, matched) for a hook entry dict.

    Tolerant of malformed entries — returns ("", False) on any structural error.
    """
    try:
        cmd = hook_entry.get("hooks", [{}])[0].get("command", "") or ""
    except Exception:
        return "", False
    return cmd, any(p in cmd for p in patterns)


def _load_hooks_section(settings_path: Path) -> tuple:
    """Load (data, hooks_dict) from settings file. Returns (None, None) on any failure."""
    if not settings_path.exists():
        return None, None
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return None, None
    return data, hooks


def _partition_entries(entries: list, patterns: list) -> tuple:
    """Split hook entries into (kept_entries, stripped_commands) for one event."""
    kept, stripped = [], []
    for entry in entries:
        cmd, matched = _hook_command_matches(entry, patterns)
        if matched:
            stripped.append(cmd)
        else:
            kept.append(entry)
    return kept, stripped


def _strip_obsolete_hooks(
    settings_path: Path, patterns: list, backup_dir: Path, dry_run: bool
) -> list:
    """Strip hook entries whose command contains any of `patterns`. Returns stripped cmds."""
    if not patterns:
        return []
    data, hooks = _load_hooks_section(settings_path)
    if data is None:
        return []
    all_stripped: list = []
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept, stripped = _partition_entries(entries, patterns)
        hooks[event] = kept
        all_stripped.extend(stripped)
    if all_stripped and not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(settings_path, backup_dir / settings_path.name)
        settings_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
    return all_stripped


def _iter_hook_commands(settings_path: Path):
    """Yield every hook command string in a settings.json file (tolerant)."""
    if not settings_path.exists():
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return
    for entries in (data.get("hooks") or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            try:
                cmd = entry.get("hooks", [{}])[0].get("command", "") or ""
            except Exception:
                cmd = ""
            if cmd:
                yield cmd


# Module-level cache: which `bd init` flags the installed binary supports.
# Populated lazily on first use; result is stable for the process lifetime.
_BD_CAPABILITIES: dict[str, bool] = {}


def _run_bd_with_timeout(args: list[str], cwd: "Path | None" = None,
                         timeout: float = 15.0) -> "subprocess.CompletedProcess | None":
    """Run a `bd` command, killing the process if it hangs past `timeout`.

    Unlike `subprocess.run(..., timeout=...)`, this kills the child process
    tree on timeout instead of leaving a zombie that holds the `.beads`
    Dolt lock. Returns None on timeout, the CompletedProcess otherwise.
    Used for the bootstrap's hang-prone `bd init` and capability-probe
    calls so a wedged Dolt server can't block the installer.
    """
    try:
        proc = subprocess.Popen(
            args, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, shell=_SHELL,
        )
    except OSError:
        return None
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=_BD_OUTPUT_GRACE)
        except subprocess.TimeoutExpired:
            pass
        return None
    return subprocess.CompletedProcess(
        args=args, returncode=proc.returncode,
        stdout=stdout or "", stderr=stderr or "",
    )


def _bd_supports_init_if_missing() -> bool:
    """Return True if the installed `bd` supports --init-if-missing (v1.1.0+).

    Used to keep bootstrap backward-compatible with Beads v1.0.x while
    enabling the idempotent path on v1.1.0+. Uses a short timeout and
    kills the child on hang so a wedged `bd` can't block the installer.
    """
    if "init_if_missing" not in _BD_CAPABILITIES:
        result = _run_bd_with_timeout(
            ["bd", "init", "--help"], timeout=_BD_TIMEOUT_SHORT,
        )
        if result is None:
            _BD_CAPABILITIES["init_if_missing"] = False
        else:
            text = (result.stdout or "") + (result.stderr or "")
            _BD_CAPABILITIES["init_if_missing"] = "--init-if-missing" in text
    return _BD_CAPABILITIES["init_if_missing"]


def _bd_init_idempotent_cmd() -> list[str]:
    """Build the `bd init` argv, adding --init-if-missing when supported."""
    cmd = ["bd", "init"]
    if _bd_supports_init_if_missing():
        cmd.append("--init-if-missing")
    return cmd


def _is_within(child: Path, root: Path) -> bool:
    """Return True if `child` resolves to `root` or any descendant of `root`."""
    try:
        c = child.resolve()
        r = root.resolve()
    except Exception:
        return False
    return c == r or r in c.parents


def _auto_inject_legacy_files(project_dir: Path, manifest: dict,
                              dry_run: bool) -> list:
    """Register OBSOLETE_FILES that exist on disk but pre-date the manifest."""
    injected: list = []
    existing = manifest.get("files", {})
    for rel in OBSOLETE_FILES:
        target = project_dir / rel
        if rel in existing:
            continue
        if not target.exists() or not _is_within(target, project_dir):
            continue
        if not dry_run:
            manifest.setdefault("files", {})[rel] = "sha256:legacy-auto-injected"
        injected.append(rel)
    return injected


def _memory_dir_should_skip(project_dir: Path) -> tuple:
    """Skip `.beads/memory` removal if knowledge.jsonl has user LEARNED data."""
    knowledge = project_dir / ".beads" / "memory" / "knowledge.jsonl"
    try:
        if knowledge.exists() and knowledge.stat().st_size > 0:
            return True, f"knowledge.jsonl contains {knowledge.stat().st_size} bytes of LEARNED data — preserved for manual review"
    except Exception:
        return False, ""
    return False, ""


def _cleanup_empty_local_settings(project_dir: Path, backup_fn,
                                  dry_run: bool) -> bool:
    """Delete .claude/settings.local.json if no real hook entries remain."""
    path = project_dir / ".claude" / "settings.local.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data == {}:
        empty = True
    elif list(data.keys()) == ["hooks"] and isinstance(data.get("hooks"), dict):
        empty = all(isinstance(v, list) and not v for v in data["hooks"].values())
    else:
        empty = False
    if not empty:
        return False
    if dry_run:
        return True
    backup_path = backup_fn() / ".claude" / "settings.local.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    path.unlink()
    return True


def _cleanup_file(rel: str, project_dir: Path, manifest: dict,
                  backup_fn, dry_run: bool) -> bool:
    """Remove one obsolete file (manifest-gated). Returns True if it was listed."""
    if rel not in manifest.get("files", {}):
        return False
    target = project_dir / rel
    if not _is_within(target, project_dir):
        print(f"[UPGRADE] Skipping suspicious path: {rel} (escapes project_dir)")
        return False
    if not target.exists():
        manifest["files"].pop(rel, None)
        return False
    if dry_run:
        return True
    backup_path = backup_fn() / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_path)
    target.unlink()
    manifest["files"].pop(rel, None)
    return True


def _cleanup_dir(rel: str, project_dir: Path, manifest: dict,
                 backup_fn, dry_run: bool) -> bool:
    """Remove one obsolete directory. Returns True if it was listed."""
    target = project_dir / rel
    if not _is_within(target, project_dir):
        print(f"[UPGRADE] Skipping suspicious path: {rel} (escapes project_dir)")
        return False
    if not target.exists() or not target.is_dir():
        return False
    if dry_run:
        return True
    backup_path = backup_fn() / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        shutil.rmtree(backup_path)
    shutil.copytree(target, backup_path)
    shutil.rmtree(target)
    prefix = rel.rstrip("/") + "/"
    for key in list(manifest.get("files", {}).keys()):
        if key.startswith(prefix):
            manifest["files"].pop(key, None)
    return True


def _cleanup_settings(settings_path: Path, patterns: list,
                      backup_fn, dry_run: bool) -> list:
    """Strip obsolete hooks from one settings file, return list of stripped commands."""
    if not patterns:
        return []
    if dry_run:
        return [c for c in _iter_hook_commands(settings_path)
                if any(p in c for p in patterns)]
    stripped = _strip_obsolete_hooks(
        settings_path, patterns, backup_fn(), dry_run
    )
    return stripped


def cleanup_obsolete(project_dir: Path, manifest: dict, dry_run: bool, timestamp: str = None) -> dict:
    """Remove obsolete files/dirs and strip obsolete settings hook entries.

    Safety rules:
    - File is removed only if its relative path is a manifest["files"] key
      (legacy installs get pre-registered via _auto_inject_legacy_files).
    - Directories are removed if they exist, except .beads/memory which is
      preserved when knowledge.jsonl still has user LEARNED data.
    - Every removal is backed up into .claude/.upgrades/<timestamp>/obsolete/<rel>.
    - Settings files are backed up before editing.
    - settings.local.json is removed outright if stripping leaves it with no
      real hook entries.
    - dry_run=True → compute report, touch nothing on disk.
    - manifest is mutated in place; caller is responsible for save_manifest.
    """
    report = {
        "removed_files": [], "removed_dirs": [], "skipped_dirs": [],
        "stripped_settings_hooks": [], "stripped_local_patterns": [],
        "removed_local_settings": False, "legacy_injected": [],
        "backups": [None],
    }

    upgrades_root = project_dir / ".claude" / ".upgrades" / (timestamp or _upgrade_timestamp())
    obsolete_backup = upgrades_root / "obsolete"
    state = {"created": False}

    def backup_fn() -> Path:
        if not state["created"] and not dry_run:
            obsolete_backup.mkdir(parents=True, exist_ok=True)
            state["created"] = True
            report["backups"][0] = str(upgrades_root)
        return obsolete_backup

    report["legacy_injected"] = _auto_inject_legacy_files(
        project_dir, manifest, dry_run,
    )
    # For accurate dry-run preview, register legacy files in manifest temporarily
    # so _cleanup_file's safety gate allows them through. Rolled back after loop.
    dry_run_injected = report["legacy_injected"] if dry_run else []
    for rel in dry_run_injected:
        manifest.setdefault("files", {})[rel] = "sha256:legacy-auto-injected"

    for rel in OBSOLETE_FILES:
        if _cleanup_file(rel, project_dir, manifest, backup_fn, dry_run):
            report["removed_files"].append(rel)

    # Roll back the dry-run temporary injection so the caller's manifest is pristine.
    for rel in dry_run_injected:
        manifest.get("files", {}).pop(rel, None)

    report["stripped_settings_hooks"] = _cleanup_settings(
        project_dir / ".claude" / "settings.json",
        OBSOLETE_SETTINGS_HOOKS, backup_fn, dry_run,
    )
    report["stripped_local_patterns"] = _cleanup_settings(
        project_dir / ".claude" / "settings.local.json",
        OBSOLETE_LOCAL_SETTINGS_PATTERNS, backup_fn, dry_run,
    )
    report["removed_local_settings"] = _cleanup_empty_local_settings(
        project_dir, backup_fn, dry_run,
    )

    for rel in OBSOLETE_DIRS:
        if rel == ".beads/memory":
            skip, reason = _memory_dir_should_skip(project_dir)
            if skip:
                print(f"[UPGRADE] Skipping .beads/memory/: {reason}")
                report["skipped_dirs"].append((rel, reason))
                continue
        if _cleanup_dir(rel, project_dir, manifest, backup_fn, dry_run):
            report["removed_dirs"].append(rel)
    return report


def run_bd_doctor(project_dir: Path) -> None:
    """Run `bd doctor` and print first 20 lines. Soft-fail on any error."""
    if not shutil.which("bd"):
        print("  bd doctor unavailable: bd not found in PATH")
        return
    try:
        result = subprocess.run(
            ["bd", "doctor"], cwd=project_dir,
            capture_output=True, text=True, shell=_SHELL,
            stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_DEFAULT,
        )
    except subprocess.TimeoutExpired:
        print("  bd doctor unavailable: timed out after 15s")
        return
    except Exception as e:
        print(f"  bd doctor unavailable: {e}")
        return

    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "non-zero exit").strip().splitlines()
        reason_first = reason[0] if reason else f"exit {result.returncode}"
        print(f"  bd doctor unavailable: {reason_first}")
        return

    print("  bd doctor:")
    for line in (result.stdout or "").splitlines()[:20]:
        print(f"    {line}")


# ============================================================================
# STEPS
# ============================================================================

def install_beads(project_dir: Path, dry_run: bool = False, jsonl: bool = False) -> bool:
    """Install beads CLI and initialize .beads directory."""
    print("\n[1/6] Installing beads...")

    if dry_run:
        # Read-only: report intent, mutate nothing (no bd init/config/hooks).
        have_bd = bool(shutil.which("bd"))
        beads_exists = (project_dir / ".beads").exists()
        sync_mode = "JSONL git-backup + Dolt" if jsonl else "Dolt-only (no JSONL)"
        print(f"  - beads CLI {'already installed' if have_bd else 'not found (would install)'}")
        print(f"  - .beads {'present' if beads_exists else 'would be initialized'}")
        print(f"  - sync config would be wired: {sync_mode} (skipped in dry-run)")
        print("  DONE")
        return True

    if not shutil.which("bd"):
        print("  - beads CLI (bd) not found, installing...")
        for method, cmd in [
            ("Homebrew", ["brew", "install", "gastownhall/beads/bd"]),
            ("npm", ["npm", "install", "-g", "@beads/bd"]),
            ("go", ["go", "install", "github.com/gastownhall/beads/cmd/bd@latest"]),
        ]:
            if shutil.which(cmd[0]):
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, shell=_SHELL,
                        stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_LONG,
                    )
                except subprocess.TimeoutExpired:
                    print(f"  - {method} install timed out, trying next method...")
                    continue
                if result.returncode == 0:
                    print(f"  - Installed via {method}")
                    break
        else:
            print("  ERROR: Could not install beads CLI (bd)")
            print("  Install manually: https://github.com/gastownhall/beads#-installation")
            return False
    else:
        print("  - beads CLI already installed")

    beads_dir = project_dir / ".beads"
    if not beads_dir.exists():
        print("  - Initializing .beads directory...")
        # --init-if-missing (Beads v1.1.0+) makes this idempotent: first
        # run creates the DB, subsequent runs are no-ops instead of
        # "database already exists" errors. Safe to omit on older bds
        # because we only pass it when the flag is supported.
        bd_init_cmd = _bd_init_idempotent_cmd()
        result = _run_bd_with_timeout(bd_init_cmd, cwd=project_dir, timeout=_BD_TIMEOUT_DEFAULT)
        if result is None:
            print("  - bd init timed out (Dolt server not running?)")
        if result is None or result.returncode != 0:
            beads_dir.mkdir(exist_ok=True)
            if jsonl:
                # Only seed the readable export when the user opted into it.
                (beads_dir / "issues.jsonl").touch()
            print("  - Created .beads manually (run 'bd init' later with Dolt server running)")

    # Wire automatic sync. Dolt is the canonical store/sync; the JSONL export is
    # opt-in (--jsonl). Best-effort; never fails the bootstrap.
    configure_beads_sync(project_dir, jsonl=jsonl)

    print("  DONE")
    return True


def _run_bd(args: list, project_dir: Path, label: str) -> bool:
    """Run a best-effort `bd` command. Never raises; returns True on rc==0."""
    if not shutil.which("bd"):
        print(f"  - bd not available, skipping {label}")
        return False
    try:
        result = subprocess.run(
            ["bd", *args], cwd=project_dir, capture_output=True, text=True,
            shell=_SHELL, stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_DEFAULT,
        )
    except subprocess.TimeoutExpired:
        print(f"  - {label} timed out (Dolt server not running?)")
        return False
    except OSError as exc:
        print(f"  - {label} failed to start: {exc}")
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        print(f"  - WARNING: {label} failed" + (f": {detail}" if detail else ""))
        return False
    return True


def _bd_config_get(project_dir: Path, key: str) -> str | None:
    """Read a bd config value (the last non-empty stdout line), or None.

    Tolerates rc!=0: bd's auto-export `git add` of a gitignored .beads/issues.jsonl
    can fail and set rc=1 while the value is still printed to stdout.
    """
    try:
        result = subprocess.run(
            ["bd", "config", "get", key], cwd=project_dir, capture_output=True,
            text=True, shell=_SHELL, stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_DEFAULT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _set_bd_config(project_dir: Path, key: str, value: str) -> bool:
    """Set a bd config value and VERIFY it stuck (read-back), not the set's rc.

    `bd config set` exits non-zero when a due auto-export tries to `git add` a
    gitignored .beads/issues.jsonl — but the config.yaml write still persists.
    Trusting that rc would print a false 'failed' warning. So we run the set
    quietly, then confirm via `bd config get`, warning only if it truly didn't
    take. Returns True when verified (or unverifiable — best-effort, no false
    alarm).
    """
    if not shutil.which("bd"):
        print(f"  - bd not available, skipping set {key}={value}")
        return False
    try:
        subprocess.run(
            ["bd", "config", "set", key, value], cwd=project_dir,
            capture_output=True, text=True, shell=_SHELL,
            stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_DEFAULT,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"  - WARNING: set {key}={value} could not run: {exc}")
        return False
    actual = _bd_config_get(project_dir, key)
    if actual is not None and actual != value:
        print(f"  - WARNING: {key} is '{actual}', expected '{value}' "
              f"— set it manually: bd config set {key} {value}")
        return False
    return True


def _git_origin_url(project_dir: Path) -> str | None:
    """Return the URL of git remote 'origin', or None if unset/unavailable."""
    return _git_config_get(project_dir, "remote.origin.url")


def _git_config_get(project_dir: Path, key: str) -> str | None:
    """Return a git config value, or None if unset/unavailable. Never raises."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", key], cwd=project_dir,
            capture_output=True, text=True,
            shell=_SHELL, stdin=subprocess.DEVNULL, timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def _install_shared_hooks(project_dir: Path) -> None:
    """Install bd's shared git hooks unless that would hijack existing hooks.

    `bd hooks install --shared` sets core.hooksPath=.beads-hooks and does NOT
    chain to a pre-existing core.hooksPath or .husky/. So we skip (with a clear
    warning) when another hook manager is already wired up.
    """
    existing = _git_config_get(project_dir, "core.hooksPath")
    husky = (project_dir / ".husky").is_dir()
    if (existing and existing != ".beads-hooks") or husky:
        print("  - WARNING: existing git hooks detected (core.hooksPath/.husky) "
              "— skipping 'bd hooks install --shared'. Wire bead sync manually "
              "with: bd hooks install")
        return
    _run_bd(["hooks", "install", "--shared"], project_dir,
            "install shared git hooks")


def configure_beads_sync(project_dir: Path, jsonl: bool = False) -> bool:
    """Wire automatic team sync (best-effort, never raises).

    Dolt is the source of truth: dolt.auto-push pushes bead writes to
    refs/dolt/* on origin and shared hooks pull on merge — that's the sync loop.

    The readable .beads/issues.jsonl export is OFF by default (redundant with
    the Dolt remote, and a tracked/ignored mismatch breaks `git add`). Pass
    jsonl=True (CLI: --jsonl) to opt back into committing it as a backup.
    Setting it explicitly (not just leaving bd's default) lets an upgrade turn
    a previously-forced export.auto=true back off on existing installs.
    """
    if not shutil.which("bd"):
        print("  - bd not available, skipping sync config "
              "(run sync setup later: bd hooks install --shared)")
        return False
    export_val = "true" if jsonl else "false"
    # git-add BEFORE auto: turning git-add off first means a "due" auto-export
    # (export.interval elapsed) won't try to `git add` a gitignored issues.jsonl
    # during the transition. Both are verified by read-back (see _set_bd_config),
    # so bd's cosmetic rc=1 from a failed post-write git-add isn't misreported.
    ok = _set_bd_config(project_dir, "export.git-add", export_val)
    ok = _set_bd_config(project_dir, "export.auto", export_val) and ok
    ok = _run_bd(["config", "set", "dolt.auto-push", "true"], project_dir,
                 "enable dolt.auto-push") and ok
    origin = _git_origin_url(project_dir)
    if origin:
        # idempotent enough: re-adding an existing remote just succeeds
        _run_bd(["dolt", "remote", "add", "origin", origin], project_dir,
                "add Dolt remote 'origin'")
    else:
        print("  - no git origin; Dolt sync stays local until a remote is added")
    _install_shared_hooks(project_dir)
    mode = "JSONL git-backup + Dolt remote" if jsonl else "Dolt remote (Dolt-only, no JSONL)"
    if ok:
        print(f"  - Sync configured ({mode} + shared hooks)")
    else:
        print("  - Sync setup attempted (some steps may need bd/Dolt running)")
    return ok


def copy_agents(recorder, project_name):
    """Copy code-reviewer and merge-supervisor templates."""
    print("\n[2/6] Agents", end="")
    start = len(recorder.changes)
    agents_dir = recorder.project_dir / ".claude" / "agents"
    if not recorder.dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
    replacements = {"[Project]": project_name}
    for agent_file in (TEMPLATES_DIR / "agents").glob("*.md"):
        dest = agents_dir / agent_file.name
        rel_key = f"agents/{agent_file.name}"
        ok, _ = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
        new_content = _render(agent_file, replacements)
        if ok:
            recorder.put_file(dest, new_content.encode("utf-8"), rel_key)
        else:
            if not recorder.dry_run:
                save_upgrade(recorder.project_dir, rel_key, new_content)
            recorder.record_skip(rel_key)
    print(f" ... {summarize_changes(recorder.changes[start:])}")


def copy_hooks(recorder, *, allow_untouched: bool = False):
    """Copy Node.js hooks (always overwrite — enforcement code), with backup + diff.

    Verifies each shipped .cjs template against ``_EXPECTED_HOOK_HASHES`` and
    refuses to install any hook whose hash differs. The point is to surface
    tampered templates early (a modified hook gets RCE on every Bash call),
    not to block legitimate local development — pass ``allow_untouched=True``
    to bypass (CLI flag: --allow-untouched-hooks).
    """
    print("\n[3/6] Hooks", end="")
    start = len(recorder.changes)
    hooks_dir = recorder.project_dir / ".claude" / "hooks"
    if not recorder.dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    hash_mismatches: list = []
    for hook_file in (TEMPLATES_DIR / "hooks").glob("*.cjs"):
        dest = hooks_dir / hook_file.name
        raw = hook_file.read_bytes()
        expected = _EXPECTED_HOOK_HASHES.get(hook_file.name)
        if expected is None:
            # Unknown hook — not in our hash table. Warn but install (the
            # alternative is refusing to bootstrap entirely when a new hook
            # is added without updating the constant).
            print(f"\n  ! WARNING: {hook_file.name} not in _EXPECTED_HOOK_HASHES", end="")
        else:
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected:
                hash_mismatches.append((hook_file.name, expected, actual))
                continue  # skip install
        recorder.put_file(dest, raw, f"hooks/{hook_file.name}")
    if hash_mismatches:
        if allow_untouched:
            for name, exp, act in hash_mismatches:
                print(
                    f"\n  ! BYPASS: {name} hash mismatch (expected {exp[:16]}…, "
                    f"got {act[:16]}…) — installing anyway because "
                    f"--allow-untouched-hooks was set",
                    end="",
                )
                # Re-loop and install the mismatched hooks. The previous loop
                # `continue`d past them, so we re-process explicitly.
                hook_file = TEMPLATES_DIR / "hooks" / name
                if hook_file.exists():
                    recorder.put_file(
                        hooks_dir / name, hook_file.read_bytes(),
                        f"hooks/{name}",
                    )
        else:
            msg_lines = [
                "ABORT: shipped hook(s) failed hash verification (supply-chain check).",
                "Re-run with --allow-untouched-hooks to install anyway (NOT recommended).",
            ]
            for name, exp, act in hash_mismatches:
                msg_lines.append(f"  - {name}: expected {exp[:16]}…  got {act[:16]}…")
            raise RuntimeError("\n".join(msg_lines))
    print(f" ... {summarize_changes(recorder.changes[start:])}")


def _copy_rule(recorder, rule_file, rules_dir):
    """Copy one rule verbatim through the recorder; record_skip if user-modified."""
    dest = rules_dir / rule_file.name
    rel_key = f"rules/{rule_file.name}"
    ok, _ = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
    if ok:
        recorder.put_file(dest, rule_file.read_bytes(), rel_key)
    else:
        if not recorder.dry_run:
            save_upgrade(recorder.project_dir, rel_key, rule_file.read_text(encoding="utf-8"))
        recorder.record_skip(rel_key)


def copy_rules_and_skills(recorder, with_rules):
    """Copy beads-workflow rule, project-discovery skill, and optional dev rules."""
    print("\n[4/6] Rules & skills", end="")
    start = len(recorder.changes)
    rules_dir = recorder.project_dir / ".claude" / "rules"
    if not recorder.dry_run:
        rules_dir.mkdir(parents=True, exist_ok=True)
    rules_src_dir = TEMPLATES_DIR / "rules"

    beads_src = rules_src_dir / "beads-workflow.md"
    if beads_src.exists():
        _copy_rule(recorder, beads_src, rules_dir)
    if with_rules:
        for rule_file in rules_src_dir.glob("*.md"):
            if rule_file.name != "beads-workflow.md":
                _copy_rule(recorder, rule_file, rules_dir)

    skill_src = TEMPLATES_DIR / "skills" / "project-discovery"
    if skill_src.exists():
        dest = recorder.project_dir / ".claude" / "skills" / "project-discovery"
        recorder.replace_tree(dest, skill_src, "skills/project-discovery")
    print(f" ... {summarize_changes(recorder.changes[start:])}")


def _json_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


_HOOK_SCRIPT_RE = re.compile(r"([\w.-]+\.cjs)")


def _hook_command(entry: dict) -> str:
    """Command string of a settings hook entry, or '' if absent/malformed."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not hooks:
        return ""
    return hooks[0].get("command", "") or ""


def _hook_script_name(cmd: str) -> str:
    """Basename of the .cjs script a hook command runs, or '' if none."""
    m = _HOOK_SCRIPT_RE.search(cmd)
    return m.group(1) if m else ""


def _find_hook_index(bucket: list, matcher, script: str, cmd: str) -> int:
    """Index in `bucket` of the entry for the same (matcher, .cjs script), or the
    same exact command for non-.cjs hooks; -1 if none. Matcher-aware so one
    script bound to several matchers (Edit + Write) keeps a slot per matcher."""
    for i, h in enumerate(bucket):
        hcmd = _hook_command(h)
        if script:
            same = h.get("matcher") == matcher and _hook_script_name(hcmd) == script
        else:
            same = hcmd == cmd
        if same:
            return i
    return -1


def _merge_settings(existing: dict, new_settings: dict) -> dict:
    """Merge new hooks into existing, per event.

    Dedup/replace by (matcher, .cjs script name) so a re-run never duplicates AND
    an upgrade REPLACES an old `node .claude/hooks/X.cjs` entry with the new
    `$CLAUDE_PROJECT_DIR`-based command for the same matcher+script (the
    loader-path migration) — while a script bound to several matchers (Edit +
    Write) keeps one entry per matcher. Non-.cjs hooks (e.g. `bd prime`) match by
    exact command. Entries with no command are skipped (never appended blindly).
    """
    for event, hooks_list in new_settings.get("hooks", {}).items():
        bucket = existing.setdefault("hooks", {}).setdefault(event, [])
        for hook in hooks_list:
            cmd = _hook_command(hook)
            if not cmd:
                continue
            idx = _find_hook_index(bucket, hook.get("matcher"), _hook_script_name(cmd), cmd)
            if idx >= 0:
                bucket[idx] = hook
            else:
                bucket.append(hook)
    return existing


def _write_settings(recorder):
    settings_dest = recorder.project_dir / ".claude" / "settings.json"
    settings_src = TEMPLATES_DIR / "settings.json"
    if not settings_src.exists():
        return
    new_settings = json.loads(settings_src.read_text(encoding="utf-8"))
    if not settings_dest.exists():
        recorder.put_file(settings_dest, _json_bytes(new_settings), "settings.json")
        return
    try:
        merged = _merge_settings(
            json.loads(settings_dest.read_text(encoding="utf-8")), new_settings
        )
        recorder.put_file(settings_dest, _json_bytes(merged), "settings.json")
    except Exception:
        action = recorder.put_file(settings_dest, _json_bytes(new_settings), "settings.json")
        if action != "unchanged":
            recorder.changes[-1]["note"] = "could not merge — replaced"


def _write_claude_md(recorder, project_name):
    claude_dest = recorder.project_dir / "CLAUDE.md"
    claude_src = TEMPLATES_DIR / "CLAUDE.md"
    if not claude_src.exists():
        return
    body = claude_src.read_text(encoding="utf-8").replace("[Project]", project_name)
    marker = "<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->"
    if not claude_dest.exists():
        recorder.put_file(claude_dest, (f"{marker}\n" + body).encode("utf-8"),
                          "CLAUDE.md", backup=False)
        return
    existing = claude_dest.read_text(encoding="utf-8")
    if marker in existing:
        return
    new_content = existing + f"\n\n---\n\n{marker}\n" + body
    recorder.put_file(claude_dest, new_content.encode("utf-8"), "CLAUDE.md", backup=False)


def copy_settings_and_claude_md(recorder, project_name):
    """Write settings.json (merge hooks) and CLAUDE.md (append if exists)."""
    print("\n[5/6] Settings", end="")
    start = len(recorder.changes)
    _write_settings(recorder)
    _write_claude_md(recorder, project_name)
    print(f" ... {summarize_changes(recorder.changes[start:])}")


def _path_is_gitignored(project_dir: Path, rel: str) -> bool:
    """True if `rel` is ignored by git in project_dir. Read-only; never raises."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=project_dir,
            capture_output=True, text=True, shell=_SHELL,
            stdin=subprocess.DEVNULL, timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _path_is_tracked(project_dir: Path, rel: str) -> bool:
    """True if `rel` is git-tracked in project_dir. Read-only; never raises."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel], cwd=project_dir,
            capture_output=True, text=True, shell=_SHELL,
            stdin=subprocess.DEVNULL, timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _report_gitignore_plan(gitignore_path: Path, entries: list) -> None:
    """[dry-run] print what setup_gitignore would do; write nothing."""
    if gitignore_path.exists():
        lines = gitignore_path.read_text(encoding="utf-8").splitlines()
        missing = [e for e in entries if e not in lines and e.rstrip("/") not in lines]
        msg = f"would add: {', '.join(missing)}" if missing else "already configured"
    else:
        msg = f"would create .gitignore with: {', '.join(entries)}"
    print(f"  - [dry-run] {msg}")
    print("  DONE")


def setup_gitignore(project_dir: Path, jsonl: bool = False, dry_run: bool = False) -> None:
    """Ensure .worktrees/ and .claude/.upgrades/ are in .gitignore.

    Default (Dolt-only): also ignore .beads/issues.jsonl — it is redundant with
    the Dolt remote and only causes `git add` conflicts. With jsonl=True the
    readable export stays git-tracked as a backup, and we instead WARN if the
    user has it ignored (that would silently break bd auto-export). Dolt
    runtime/binary files are excluded by .beads/.gitignore (written by bd init).
    """
    print("\n[6/6] Setting up .gitignore...")
    gitignore_path = project_dir / ".gitignore"
    entries = [".worktrees/", ".claude/.upgrades/"]
    if not jsonl:
        entries.append(".beads/issues.jsonl")

    if dry_run:
        _report_gitignore_plan(gitignore_path, entries)
        return

    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding='utf-8')
        lines = content.splitlines()
        missing = [
            e for e in entries
            if e not in lines and e.rstrip("/") not in lines
        ]
        if missing:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                if content and not content.endswith("\n"):
                    f.write("\n")
                f.write("\n# Beads orchestration\n")
                for entry in missing:
                    f.write(f"{entry}\n")
                    print(f"  - Added {entry}")
        else:
            print("  - Already configured")
    else:
        body = "".join(f"{e}\n" for e in entries)
        gitignore_path.write_text(f"# Beads orchestration\n{body}", encoding='utf-8')
        print("  - Created .gitignore")

    if jsonl:
        # In --jsonl mode the export must stay git-tracked. A pre-existing ignore
        # of it breaks bd auto-sync (the "paths are ignored" warnings in [1/6]).
        # We don't edit the user's rules — just surface the conflict with the fix.
        if _path_is_gitignored(project_dir, ".beads/issues.jsonl"):
            print("  - WARNING: .beads/issues.jsonl is gitignored — bd's readable "
                  "export/auto-sync will fail. Remove that ignore rule so the "
                  "backup stays git-tracked.")
    elif _path_is_tracked(project_dir, ".beads/issues.jsonl"):
        # Default (Dolt-only): the file is now ignored, but a previous install may
        # have committed it — ignoring alone won't untrack it. Only nudge when it
        # is actually tracked, so the `git rm --cached` advice can't misfire.
        print("  - NOTE: .beads/issues.jsonl is now gitignored (Dolt is the source "
              "of truth) but still git-tracked. Stop tracking it: "
              "git rm --cached .beads/issues.jsonl")

    print("  DONE")


# ============================================================================
# MAIN
# ============================================================================

def _print_cleanup_report(report: dict, dry_run: bool) -> None:
    """Print a [UPGRADE] Cleanup: block from cleanup_obsolete report."""
    prefix = "[DRY-RUN] " if dry_run else ""
    print("\n[UPGRADE] Cleanup:")
    for rel in report.get("legacy_injected", []):
        print(f"  {prefix}auto-injected legacy file into manifest: {rel}")
    for rel in report["removed_files"]:
        print(f"  {prefix}removed file: {rel}")
    for rel in report["removed_dirs"]:
        print(f"  {prefix}removed dir:  {rel}")
    for rel, reason in report.get("skipped_dirs", []):
        print(f"  {prefix}skipped dir:  {rel} ({reason})")
    for cmd in report["stripped_settings_hooks"]:
        print(f"  {prefix}stripped settings hook: {cmd}")
    for cmd in report["stripped_local_patterns"]:
        print(f"  {prefix}stripped local hook:    {cmd}")
    if report.get("removed_local_settings"):
        print(f"  {prefix}removed file: .claude/settings.local.json (no hooks left)")
    backup = report["backups"][0]
    if backup:
        print(f"  backup: {backup}")
    if not any([
        report["removed_files"], report["removed_dirs"],
        report.get("skipped_dirs"),
        report["stripped_settings_hooks"], report["stripped_local_patterns"],
        report.get("removed_local_settings"),
        report.get("legacy_injected"),
    ]):
        print("  nothing to clean")


def bootstrap_project(
    project_dir: Path, project_name: str | None, with_rules: bool,
    force: bool, upgrade: bool, dry_run: bool, no_diff: bool = False,
    jsonl: bool = False, harness: str = "claude",
    allow_untouched_hooks: bool = False,
) -> int:
    """Run bootstrap for a single project. Returns exit code (0 = success)."""
    if not dry_run:
        project_dir.mkdir(parents=True, exist_ok=True)
    resolved_name = project_name or infer_project_name(project_dir)

    print(f"\nBootstrapping beads orchestration for: {resolved_name}")
    print(f"Directory: {project_dir}")
    print(f"Harness adapter: {harness}")
    if force:
        print("Mode: FORCE (overwriting all files)")
    if upgrade:
        print("Mode: UPGRADE" + (" (dry-run)" if dry_run else ""))
    print("=" * 60)

    if not TEMPLATES_DIR.exists():
        print(f"\nERROR: Templates not found: {TEMPLATES_DIR}")
        return 1

    manifest = load_manifest(project_dir)
    recorder = ChangeRecorder(project_dir, manifest, force=force,
                              dry_run=dry_run, no_diff=no_diff)

    if not install_beads(project_dir, dry_run=dry_run, jsonl=jsonl):
        return 1

    try:
        copy_agents(recorder, resolved_name)
        copy_hooks(recorder, allow_untouched=allow_untouched_hooks)
        copy_rules_and_skills(recorder, with_rules)
        copy_settings_and_claude_md(recorder, resolved_name)
        install_harness_adapters(recorder, _resolve_harnesses(harness), resolved_name)
        setup_gitignore(project_dir, jsonl=jsonl, dry_run=dry_run)
        recorder.print_report()

        # Read version from package.json (same package as bootstrap.py)
        pkg_json = SCRIPT_DIR / "package.json"
        pkg_version = None
        if pkg_json.exists():
            try:
                pkg_version = json.loads(pkg_json.read_text(encoding="utf-8")).get("version")
            except Exception:
                pass

        # Run upgrade cleanup AFTER init steps so manifest reflects our files.
        # Legacy installs without manifest are handled by _auto_inject_legacy_files
        # inside cleanup_obsolete — the OBSOLETE_* paths are dev-controlled and safe.
        if upgrade:
            report = cleanup_obsolete(project_dir, manifest, dry_run, timestamp=recorder.timestamp)
            _print_cleanup_report(report, dry_run)

        manifest["version"] = pkg_version
        manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
        if not dry_run:
            save_manifest(project_dir, manifest)

        print("\n" + "=" * 60)
        print("BOOTSTRAP COMPLETE")
        print("=" * 60)

        # Post-upgrade health check — never fatal
        if upgrade and not dry_run:
            print("")
            run_bd_doctor(project_dir)

        print(f"""
Next steps:

1. Restart Claude Code to load hooks and agents
2. Run /project-discovery to extract project conventions
3. Create your first bead: bd create "Task" -d "Description"
4. Dispatch work: Task(subagent_type="general-purpose", prompt="BEAD_ID: ...")
""")
        return 0
    except Exception as e:
        # Mid-step failure: surface the change report so the user sees
        # what landed, and best-effort save the manifest so the next run
        # doesn't churn through those files as 'modified'.
        print(f"\n[BOOTSTRAP FAILED] {type(e).__name__}: {e}")
        recorder.print_report()
        if not dry_run:
            try:
                save_manifest(project_dir, manifest)
            except Exception as save_err:
                print(f"  (manifest save also failed: {save_err})")
        return 1


def run_batch_upgrade(
    parent_dir: Path, with_rules: bool, force: bool, dry_run: bool, no_diff: bool = False,
    jsonl: bool = False, harness: str = "claude",
) -> int:
    """Iterate direct subdirs of parent_dir that contain .beads/ and upgrade each."""
    if not parent_dir.exists() or not parent_dir.is_dir():
        print(f"ERROR: --all parent directory not found: {parent_dir}")
        return 1

    print(f"\n[BATCH UPGRADE] Scanning {parent_dir}")
    candidates = sorted(p for p in parent_dir.iterdir() if p.is_dir())
    upgraded = 0
    skipped: list = []

    for child in candidates:
        if not (child / ".beads").is_dir():
            skipped.append((child.name, "no .beads/"))
            continue
        print(f"\n{'#' * 60}\n# {child.name}\n{'#' * 60}")
        try:
            rc = bootstrap_project(
                project_dir=child, project_name=None, with_rules=with_rules,
                force=force, upgrade=True, dry_run=dry_run, no_diff=no_diff,
                jsonl=jsonl, harness=harness,
                allow_untouched_hooks=False,
            )
            if rc == 0:
                upgraded += 1
            else:
                skipped.append((child.name, f"exit {rc}"))
        except Exception as e:
            skipped.append((child.name, f"exception: {e}"))

    print("\n" + "=" * 60)
    print(f"BATCH UPGRADE SUMMARY: {upgraded} upgraded, {len(skipped)} skipped")
    print("=" * 60)
    for name, reason in skipped:
        print(f"  - {name}: {reason}")
    return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrap beads orchestration")
    parser.add_argument("--project-name", default=None, help="Project name (auto-inferred if not provided)")
    parser.add_argument("--project-dir", default=".", help="Project directory")
    parser.add_argument("--with-rules", action="store_true", help="Also copy dev rules (implementation-standard, logging, tdd)")
    parser.add_argument("--force", action="store_true", help="Overwrite all files regardless of user modifications")
    parser.add_argument("--upgrade", action="store_true", help="Run init flow then cleanup obsolete items (uses existing manifest)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing anything")
    parser.add_argument("--no-diff", action="store_true", help="Suppress full per-file diffs (summary + backups still shown)")
    parser.add_argument("--all", dest="all_parent", default=None, metavar="PARENT_DIR", help="Batch upgrade: iterate direct subdirs of PARENT_DIR that contain .beads/. Implies --upgrade.")
    parser.add_argument("--jsonl", action="store_true", help="Opt into the readable .beads/issues.jsonl git-backup (default: Dolt-only sync, JSONL export disabled)")
    parser.add_argument("--harness", default="claude", help="Harness adapter to install (claude, codex, opencode, pi, omp, omo, all). Default: claude (v3.x compat).")
    parser.add_argument("--allow-untouched-hooks", action="store_true", help="Bypass the SHA-256 hook-tamper check (for local hook development only). Default: refused.")
    parser.add_argument("--color", dest="color", action="store_const", const="always", default="auto", help="Force ANSI color output (default: auto-detect a TTY)")
    parser.add_argument("--no-color", dest="color", action="store_const", const="never", help="Disable ANSI color output (also honors the NO_COLOR env var)")
    args = parser.parse_args()

    configure_color(args.color)

    if args.all_parent:
        parent = Path(args.all_parent).resolve()
        sys.exit(run_batch_upgrade(
            parent_dir=parent, with_rules=args.with_rules,
            force=args.force, dry_run=args.dry_run, no_diff=args.no_diff,
            jsonl=args.jsonl, harness=args.harness,
        ))

    project_dir = Path(args.project_dir).resolve()
    sys.exit(bootstrap_project(
        project_dir=project_dir, project_name=args.project_name,
        with_rules=args.with_rules, force=args.force,
        upgrade=args.upgrade, dry_run=args.dry_run, no_diff=args.no_diff,
        jsonl=args.jsonl, harness=args.harness,
        allow_untouched_hooks=args.allow_untouched_hooks,
    ))


def _expand_comp(adapter, by_id):
    """Expand a composing adapter to [adapter, *composes_resolved] in order.
    Used by install_harness_adapters so a single "omo" entry also installs
    the composed opencode + codex configs without callers having to walk
    adapters.expand() manually.
    """
    seen = {adapter.id}
    out = [adapter]
    for member_id in getattr(adapter, "composes", ()):
        member = by_id.get(member_id)
        if member is not None and member.id not in seen:
            out.append(member)
            seen.add(member.id)
    return out



# install_harness_adapters — per-harness plugin/extension installer
# ============================================================================
# Iterates a list of HarnessAdapter instances (resolved from --harness) and
# emits the per-harness plugin/extension/agents/skills/rules files into
# project_dir. The legacy `claude` adapter is byte-equivalent to v3.x — it
# uses the existing copy_agents / copy_hooks / copy_rules_and_skills /
# copy_settings_and_claude_md / setup_gitignore flow and writes nothing extra
# for itself. New adapters (opencode, omp, …) emit per-harness trees
# (`.opencode/`, `.omp/`, …) without re-running the legacy flow.
#
# Each non-claude adapter also installs the canonical "agents/", "skills/"
# contents into the per-harness directory so the harness auto-discovers them
# (OpenCode: `.opencode/agents/`, OMP: `.omp/agents/`, …).

_BD_BEADS_INTEGRATION_MARKERS = (
    "<!-- BEGIN BEADS INTEGRATION",
    "<!-- END BEADS INTEGRATION -->",
)

_BD_SETUP_RECIPES_BY_ADAPTER = {
    "opencode": "opencode",
    "omp": "opencode",   # oh-my-pi embeds OpenCode as its primary harness
    "omo": "opencode",   # OMO Ultimate / Light are OpenCode-based
    "codex": "codex",
}


def _copy_template_file(recorder: "ChangeRecorder", src: Path, dest: Path, rel_key: str) -> None:
    """Copy one file through the recorder; record_skip if user-modified."""
    if not src.exists():
        return
    ok, _ = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
    content = src.read_bytes()
    if ok:
        recorder.put_file(dest, content, rel_key)
    else:
        if not recorder.dry_run:
            save_upgrade(recorder.project_dir, rel_key, content.decode("utf-8", "replace"))
        recorder.record_skip(rel_key)


def _copy_template_dir(recorder: "ChangeRecorder", src: Path, dest: Path, prefix: str) -> None:
    """Recursively copy a template directory through the recorder."""
    if not src.exists() or not src.is_dir():
        return
    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src).as_posix()
        dest_file = dest / rel
        _copy_template_file(recorder, src_file, dest_file, f"{prefix}/{rel}")


def _write_settings_for_adapter(recorder: "ChangeRecorder", adapter: "_HarnessAdapter", project_dir: Path) -> None:
    """Emit the per-harness settings/config file (json or yml)."""
    if not adapter.settings_source:
        return
    if adapter.id == "claude":
        # Legacy flow already wrote .claude/settings.json; skip to keep v3.x parity.
        return
    dest = adapter.settings_destination(recorder.project_dir)
    src = adapter.settings_source
    rel_key = adapter.settings_rel_key()
    if not src.exists():
        return
    # Codex ships only a 4-line comment placeholder. If the user already has a
    # real Codex CLI config.toml at .codex/config.toml, NEVER clobber it
    # (Codex has no JSON-style merge — `recorder.put_file` would replace the
    # whole file, silently destroying their model / sandbox / approval_policy
    # settings). For other harnesses (opencode, omp, omo, pi) the shipped
    # template IS the intended config, so we still write it.
    if adapter.id == "codex" and dest.exists():
        existing = dest.read_text(encoding="utf-8").strip()
        # Anything beyond the placeholder comments = user-owned content.
        non_comment_lines = [
            line for line in existing.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if non_comment_lines:
            # Existing user config: leave it alone. Only mark in manifest so
            # the user sees we noticed the file.
            if not recorder.dry_run:
                recorder.changes.append({
                    "key": rel_key, "action": "preserved",
                    "label": "existing user config", "added": 0, "removed": 0,
                    "diff": [], "backup": None,
                })
            return
    # Inject the per-harness path of the plugin/extension into the config so the
    # harness auto-loads it. Only do this when the template still references
    # the claude-style `.cjs` hook path.
    raw = src.read_text(encoding="utf-8")
    if adapter.id == "opencode":
        # opencode.json lives at the project root; the plugin path is
        # therefore relative to that root (.opencode/plugins/...).
        if '"plugin"' not in raw:
            raw = raw.rstrip().rstrip("}").rstrip("]").rstrip(",") + ',\n  "plugin": ["./.opencode/plugins/claude-protocol.js"]\n}'
    elif adapter.id in ("omp", "omo", "pi"):
        # config.yml is at .omp/config.yml, so extensions/ is a sibling.
        if "claude-protocol" not in raw and "extensions:" in raw:
            raw = raw.rstrip() + "\n  - ./extensions/claude-protocol.js\n"
    recorder.put_file(dest, raw.encode("utf-8"), rel_key)


def _write_beads_marker_for_adapter(recorder: "ChangeRecorder", adapter: "_HarnessAdapter", project_dir: Path) -> None:
    """Run `bd setup <recipe>` and write the resulting AGENTS.md marker into the
    harness-specific agent-instructions file (idempotent if already present)."""
    recipe = _BD_SETUP_RECIPES_BY_ADAPTER.get(adapter.id)
    if not recipe or not shutil.which("bd"):
        return
    out_path = adapter.agent_instructions_destination(project_dir)
    if out_path.exists() and "BEGIN BEADS INTEGRATION" in out_path.read_text(encoding="utf-8"):
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not recorder.dry_run:
        try:
            subprocess.run(
                ["bd", "setup", recipe, "-o", str(out_path)],
                cwd=project_dir, capture_output=True, text=True,
                shell=_SHELL, stdin=subprocess.DEVNULL, timeout=_BD_TIMEOUT_DEFAULT,
            )
        except (subprocess.TimeoutExpired, OSError):
            return
    if out_path.exists():
        rel_key = adapter.agent_instructions_rel_key()
        try:
            recorder.put_file(out_path, out_path.read_bytes(), rel_key)
        except Exception:
            pass


def _write_agent_instructions_for_adapter(
    recorder: "ChangeRecorder", adapter: "_HarnessAdapter", project_name: str,
) -> None:
    """Append the claude-protocol orchestrator marker on top of whatever
    ``bd setup <recipe>`` (or a previous install) wrote into the
    per-harness AGENTS.md / CLAUDE.md. Idempotent: if the marker is
    already there, no work is done.
    """
    src = adapter.agent_instructions_source
    if not src or not src.exists():
        return
    body = src.read_text(encoding="utf-8").replace("[Project]", project_name)
    marker = "<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->"
    dest = adapter.agent_instructions_destination(recorder.project_dir)
    rel_key = adapter.agent_instructions_rel_key()
    if recorder.dry_run:
        # No-op: the marker logic runs only when actually writing.
        return
    if dest.exists():
        existing = dest.read_text(encoding="utf-8")
        if marker in existing:
            return  # already installed
        new_content = existing.rstrip() + f"\n\n---\n\n{marker}\n{body}"
        recorder.put_file(dest, new_content.encode("utf-8"), rel_key, backup=False)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        recorder.put_file(dest, f"{marker}\n{body}".encode("utf-8"), rel_key, backup=False)


def _ensure_agent_instructions_exist(
    recorder: "ChangeRecorder", adapter: "_HarnessAdapter", project_name: str,
) -> None:
    """Write the body of AGENTS.md / CLAUDE.md so external ``bd setup <recipe>``
    can later replace the file. Only writes when the file does NOT already
    exist on disk — never overwrites a Beads-managed or user-authored file.
    """
    src = adapter.agent_instructions_source
    if not src or not src.exists():
        return
    body = src.read_text(encoding="utf-8").replace("[Project]", project_name)
    marker = "<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->"
    dest = adapter.agent_instructions_destination(recorder.project_dir)
    rel_key = adapter.agent_instructions_rel_key()
    if dest.exists():
        return
    if not recorder.dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
    recorder.put_file(dest, f"{marker}\n{body}".encode("utf-8"), rel_key, backup=False)


def install_harness_adapters(
    recorder: "ChangeRecorder", adapters: list, project_name: str,
) -> None:
    """Install per-harness adapters in declaration order.

    `adapters` may be either :class:`HarnessAdapter` instances or plain id
    strings (e.g. ``"opencode"``); strings are resolved via the
    ``adapters.resolve()`` helper so callers can pass either shape.
    """
    if _HarnessAdapter is None:
        raise RuntimeError(
            "adapters module not importable; bootstrap.py is missing adapters.py"
        )

    resolved: list = []
    by_id = {a.id: a for a in _ALL_ADAPTERS}
    for entry in adapters:
        if isinstance(entry, str):
            if entry not in by_id:
                raise ValueError(f"unknown harness: {entry!r}")
            # Expand any composing adapter inline so a single --harness omo
            # call also installs the composed opencode + codex configs.
            for member in _expand_comp(by_id[entry], by_id):
                resolved.append(member)
        elif isinstance(entry, _HarnessAdapter):
            for member in _expand_comp(entry, by_id):
                resolved.append(member)
        else:
            raise TypeError(
                f"install_harness_adapters expected HarnessAdapter or str, got {type(entry).__name__}"
            )

    seen: set[str] = set()
    for adapter in resolved:
        if adapter.id in seen:
            continue
        seen.add(adapter.id)
        if adapter.id == "claude":
            continue  # legacy flow is invoked by bootstrap_project

        project_dir = recorder.project_dir
        adapter_root = project_dir / adapter.install_root
        template_root = adapter.adapter_dir

        # 1. plugin/extension entry + shared runtime policy
        #
        # We ship ONE entry per harness (the bundled .js) and skip the
        # .ts source. OpenCode and OMP both discover .ts and .js in the
        # same directory; emitting both causes the same hook to fire
        # twice (one module per loader). The .ts source remains in
        # ``templates/`` for the maintainers' benefit; consumers get
        # the pre-bundled JS so double-loading is impossible.
        if adapter.id == "opencode":
            for rel in ("plugins/claude-protocol.js", "shared/runtime-policy.js"):
                src = template_root / rel
                if src.exists():
                    _copy_template_file(
                        recorder, src, adapter_root / rel,
                        f"{adapter.install_root}/{rel}",
                    )
        elif adapter.id in ("omp", "omo"):
            for rel in ("extensions/claude-protocol.js", "shared/runtime-policy.js"):
                src = template_root / rel
                if src.exists():
                    _copy_template_file(
                        recorder, src, adapter_root / rel,
                        f"{adapter.install_root}/{rel}",
                    )

        # 2. per-harness settings/config
        _write_settings_for_adapter(recorder, adapter, project_dir)

        # 3. canonical agents/, skills/, rules/ (shared content emitted to the
        #    per-harness root so the harness auto-discovers them).
        shared_agents = TEMPLATES_DIR / "agents"
        shared_skills = TEMPLATES_DIR / "skills"
        shared_rules = TEMPLATES_DIR / "rules"
        if (template_root / "agents").exists():
            _copy_template_dir(recorder, template_root / "agents", adapter_root / "agents",
                               f"{adapter.install_root}/agents")
        elif shared_agents.exists() and adapter.id in ("opencode", "omp", "omo", "pi"):
            _copy_template_dir(recorder, shared_agents, adapter_root / "agents",
                               f"{adapter.install_root}/agents")
        if (template_root / "skills").exists():
            _copy_template_dir(recorder, template_root / "skills", adapter_root / "skills",
                               f"{adapter.install_root}/skills")
        elif shared_skills.exists() and adapter.id in ("opencode", "omp", "omo", "pi"):
            _copy_template_dir(recorder, shared_skills, adapter_root / "skills",
                               f"{adapter.install_root}/skills")
        if adapter.uses_shared_rules and shared_rules.exists():
            _copy_template_dir(recorder, shared_rules, adapter_root / "rules",
                               f"{adapter.install_root}/rules")

        # 4. Write the body of AGENTS.md / CLAUDE.md FIRST so the file
        #    exists. If the Beads CLI then runs and rewrites the file
        #    with its own block, that's fine — the orchestrator marker
        #    is re-appended in step 6.
        _ensure_agent_instructions_exist(recorder, adapter, project_name)

        # 5. Beads integration marker via `bd setup <recipe>`. The Beads CLI
        #    overwrites the AGENTS.md from scratch with its own marker block;
        #    we run the CLI when supported (no-op if already installed). Both
        #    `omp` and `omo` use the OpenCode Beads recipe because OMP embeds
        #    the opencode harness.
        if adapter.id in ("opencode", "omp", "omo"):
            _write_beads_marker_for_adapter(recorder, adapter, project_dir)

        # 6. Re-append the orchestrator marker on top of whatever the Beads
        #    CLI just wrote (or skip if the file is missing). Idempotent.
        _write_agent_instructions_for_adapter(recorder, adapter, project_name)


# Lookup table used by bootstrap_project to resolve string ids to adapter
# objects (the install_harness_adapters API accepts both shapes).
_BY_ID: dict[str, "_HarnessAdapter"] = (
    {a.id: a for a in _ALL_ADAPTERS} if _ALL_ADAPTERS else {}
)


# IMPORTANT: must stay at end of file. Defines like install_harness_adapters
# below this point would NameError on script-mode invocation (python
# bootstrap.py ...), because the script exits at main() before those defs
# are ever bound.
if __name__ == "__main__":
    main()
