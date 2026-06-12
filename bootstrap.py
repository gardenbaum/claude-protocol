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
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

_SHELL = sys.platform == "win32"
SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATES_DIR = SCRIPT_DIR / "templates"


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
        rel = dest.resolve().relative_to(self.project_dir)
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
        if not self.dry_run:
            self._atomic_write(dest, new_bytes)
            if backup:
                self.manifest["files"][key] = bytes_sha256(new_bytes)
        self.changes.append({"key": key, "action": action, "label": label,
                             "added": added, "removed": removed,
                             "diff": diff, "backup": backup_path})
        return action

    @staticmethod
    def _summary_line(c):
        label = f"  {c['label']}" if c.get("label") else ""
        counts = "" if c["action"] == "new" else f"  +{c['added']} -{c['removed']}"
        return f"  {c['action']:<12} {c['key']}{label}{counts}"

    def _print_diffs(self, changed):
        for c in changed:
            if c["diff"]:
                print("")
                for line in c["diff"]:
                    print(line)

    def print_report(self):
        changed = [c for c in self.changes if c["action"] != "unchanged"]
        tally = {}
        for c in changed:
            tally[c["action"]] = tally.get(c["action"], 0) + 1
        summary = ", ".join(f"{n} {a}" for a, n in sorted(tally.items())) or "no changes"
        backup = str(self.backup_root) if self._backup_created else "(none)"
        prefix = "[DRY-RUN] " if self.dry_run else ""
        print(f"\n{prefix}[CHANGES] {summary}   backup: {backup}")
        for c in changed:
            print(self._summary_line(c))
        if not self.no_diff:
            self._print_diffs(changed)


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
            stdin=subprocess.DEVNULL, timeout=15,
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

def install_beads(project_dir: Path) -> bool:
    """Install beads CLI and initialize .beads directory."""
    print("\n[1/6] Installing beads...")

    if not shutil.which("bd"):
        print("  - beads CLI (bd) not found, installing...")
        for method, cmd in [
            ("Homebrew", ["brew", "install", "gastownhall/beads/bd"]),
            ("npm", ["npm", "install", "-g", "@beads/bd"]),
            ("go", ["go", "install", "github.com/gastownhall/beads/cmd/bd@latest"]),
        ]:
            if shutil.which(cmd[0]):
                result = subprocess.run(cmd, capture_output=True, text=True, shell=_SHELL)
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
        try:
            result = subprocess.run(
                ["bd", "init"], cwd=project_dir,
                capture_output=True, text=True, shell=_SHELL,
                stdin=subprocess.DEVNULL, timeout=15,
            )
        except subprocess.TimeoutExpired:
            result = None
            print("  - bd init timed out (Dolt server not running?)")
        if result is None or result.returncode != 0:
            beads_dir.mkdir(exist_ok=True)
            (beads_dir / "issues.jsonl").touch()
            print("  - Created .beads manually (run 'bd init' later with Dolt server running)")

    # Wire automatic sync + readable git backup (bd 1.0.5: export.* default off,
    # Dolt is the canonical store/sync). Best-effort; never fails the bootstrap.
    configure_beads_sync(project_dir)

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
            shell=_SHELL, stdin=subprocess.DEVNULL, timeout=15,
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


def _git_origin_url(project_dir: Path) -> str | None:
    """Return the URL of git remote 'origin', or None if unset/unavailable."""
    return _git_config_get(project_dir, "remote.origin.url")


def _git_config_get(project_dir: Path, key: str) -> str | None:
    """Return a git config value, or None if unset/unavailable. Never raises."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", key], cwd=project_dir,
            capture_output=True, text=True,
            shell=_SHELL, stdin=subprocess.DEVNULL, timeout=10,
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


def configure_beads_sync(project_dir: Path) -> bool:
    """Wire automatic team sync + readable git backup (best-effort, never raises).

    export.* commits readable .beads/issues.jsonl; dolt.auto-push pushes bead
    writes to refs/dolt/data on origin; shared hooks pull on merge — sync loop.
    """
    if not shutil.which("bd"):
        print("  - bd not available, skipping sync config "
              "(run sync setup later: bd hooks install --shared)")
        return False
    ok = _run_bd(["config", "set", "export.auto", "true"], project_dir,
                 "enable export.auto")
    ok = _run_bd(["config", "set", "export.git-add", "true"], project_dir,
                 "enable export.git-add") and ok
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
    if ok:
        print("  - Sync configured (JSONL git-backup + Dolt remote + shared hooks)")
    else:
        print("  - Sync setup attempted (some steps may need bd/Dolt running)")
    return ok


def copy_agents(recorder, project_name):
    """Copy code-reviewer and merge-supervisor templates."""
    print("\n[2/6] Copying agents...")
    agents_dir = recorder.project_dir / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    skipped = []
    replacements = {"[Project]": project_name}
    for agent_file in (TEMPLATES_DIR / "agents").glob("*.md"):
        dest = agents_dir / agent_file.name
        rel_key = f"agents/{agent_file.name}"
        ok, reason = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
        new_content = _render(agent_file, replacements)
        if ok:
            recorder.put_file(dest, new_content.encode("utf-8"), rel_key)
            print(f"  - {agent_file.name}" + (f" ({reason})" if reason != "new" else ""))
        else:
            save_upgrade(recorder.project_dir, rel_key, new_content)
            skipped.append(rel_key)
            print(f"  - {agent_file.name} (MODIFIED by user — skipped)")
            print(f"    New version saved to: .claude/.upgrades/{rel_key}")
    print("  DONE")
    return skipped


def copy_hooks(recorder):
    """Copy Node.js hooks (always overwrite — enforcement code), with backup + diff."""
    print("\n[3/6] Copying hooks...")
    hooks_dir = recorder.project_dir / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for hook_file in (TEMPLATES_DIR / "hooks").glob("*.cjs"):
        dest = hooks_dir / hook_file.name
        recorder.put_file(dest, hook_file.read_bytes(), f"hooks/{hook_file.name}")
        print(f"  - {hook_file.name}")
    print("  DONE")


def _copy_rule(recorder, rule_file, rules_dir):
    """Copy one rule verbatim through the recorder; return [rel_key] if skipped."""
    dest = rules_dir / rule_file.name
    rel_key = f"rules/{rule_file.name}"
    ok, reason = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
    if ok:
        recorder.put_file(dest, rule_file.read_bytes(), rel_key)
        suffix = f" ({reason})" if reason != "new" else ""
        print(f"  - rules/{rule_file.name}{suffix}")
        return []
    save_upgrade(recorder.project_dir, rel_key, rule_file.read_text(encoding="utf-8"))
    print(f"  - rules/{rule_file.name} (MODIFIED by user — skipped)")
    print(f"    New version saved to: .claude/.upgrades/{rel_key}")
    return [rel_key]


def copy_rules_and_skills(recorder, with_rules):
    """Copy beads-workflow rule, project-discovery skill, and optional dev rules."""
    print("\n[4/6] Copying rules and skills...")
    rules_dir = recorder.project_dir / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    skipped = []
    rules_src_dir = TEMPLATES_DIR / "rules"

    beads_src = rules_src_dir / "beads-workflow.md"
    if beads_src.exists():
        skipped += _copy_rule(recorder, beads_src, rules_dir)
    if with_rules:
        for rule_file in rules_src_dir.glob("*.md"):
            if rule_file.name != "beads-workflow.md":
                skipped += _copy_rule(recorder, rule_file, rules_dir)

    skill_src = TEMPLATES_DIR / "skills" / "project-discovery"
    if skill_src.exists():
        dest = recorder.project_dir / ".claude" / "skills" / "project-discovery"
        recorder.replace_tree(dest, skill_src, "skills/project-discovery")
        print("  - skills/project-discovery/")
    print("  DONE")
    return skipped


def _json_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def _merge_settings(existing: dict, new_settings: dict) -> dict:
    """Merge new hooks into existing by event, skipping commands already present."""
    for event, hooks_list in new_settings.get("hooks", {}).items():
        existing.setdefault("hooks", {}).setdefault(event, [])
        existing_commands = {
            h["hooks"][0]["command"]
            for h in existing["hooks"][event]
            if h.get("hooks") and h["hooks"][0].get("command")
        }
        for hook in hooks_list:
            cmd = hook.get("hooks", [{}])[0].get("command", "")
            if cmd not in existing_commands:
                existing["hooks"][event].append(hook)
    return existing


def _write_settings(recorder):
    settings_dest = recorder.project_dir / ".claude" / "settings.json"
    settings_src = TEMPLATES_DIR / "settings.json"
    if not settings_src.exists():
        return
    new_settings = json.loads(settings_src.read_text(encoding="utf-8"))
    if not settings_dest.exists():
        recorder.put_file(settings_dest, _json_bytes(new_settings), "settings.json")
        print("  - settings.json")
        return
    try:
        merged = _merge_settings(
            json.loads(settings_dest.read_text(encoding="utf-8")), new_settings
        )
        recorder.put_file(settings_dest, _json_bytes(merged), "settings.json")
        print("  - settings.json (merged hooks)")
    except Exception:
        recorder.put_file(settings_dest, _json_bytes(new_settings), "settings.json")
        print("  - settings.json (replaced — could not merge)")


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
        print("  - CLAUDE.md (created)")
        return
    existing = claude_dest.read_text(encoding="utf-8")
    if marker in existing:
        print("  - CLAUDE.md (orchestration section present, skipped)")
        return
    new_content = existing + f"\n\n---\n\n{marker}\n" + body
    recorder.put_file(claude_dest, new_content.encode("utf-8"), "CLAUDE.md", backup=False)
    print("  - CLAUDE.md (appended orchestration section)")


def copy_settings_and_claude_md(recorder, project_name):
    """Write settings.json (merge hooks) and CLAUDE.md (append if exists)."""
    print("\n[5/6] Copying settings and CLAUDE.md...")
    _write_settings(recorder)
    _write_claude_md(recorder, project_name)
    print("  DONE")


def setup_gitignore(project_dir: Path) -> None:
    """Ensure .worktrees/ and .claude/.upgrades/ are in .gitignore.

    .beads/ is intentionally NOT ignored — the readable .beads/issues.jsonl
    export must stay git-tracked as a backup (bd 1.0.5: opt-in export, no stray
    root /issues.jsonl). Dolt runtime/binary files are excluded by
    .beads/.gitignore (written by bd init).
    """
    print("\n[6/6] Setting up .gitignore...")
    gitignore_path = project_dir / ".gitignore"
    entries = [".worktrees/", ".claude/.upgrades/"]

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
        gitignore_path.write_text(
            "# Beads orchestration\n.worktrees/\n.claude/.upgrades/\n",
            encoding='utf-8',
        )
        print("  - Created .gitignore")

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
) -> int:
    """Run bootstrap for a single project. Returns exit code (0 = success)."""
    project_dir.mkdir(parents=True, exist_ok=True)
    resolved_name = project_name or infer_project_name(project_dir)

    print(f"\nBootstrapping beads orchestration for: {resolved_name}")
    print(f"Directory: {project_dir}")
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
    all_skipped = []

    if not install_beads(project_dir):
        return 1

    all_skipped += copy_agents(recorder, resolved_name)
    copy_hooks(recorder)
    all_skipped += copy_rules_and_skills(recorder, with_rules)
    copy_settings_and_claude_md(recorder, resolved_name)
    setup_gitignore(project_dir)
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

    if all_skipped:
        print(f"\n  {len(all_skipped)} file(s) skipped (user-modified):")
        for rel in all_skipped:
            print(f"    - {rel}")
            print(f"      Review: diff .claude/{rel} .claude/.upgrades/{rel}")

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


def run_batch_upgrade(
    parent_dir: Path, with_rules: bool, force: bool, dry_run: bool, no_diff: bool = False,
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
    args = parser.parse_args()

    if args.all_parent:
        parent = Path(args.all_parent).resolve()
        sys.exit(run_batch_upgrade(
            parent_dir=parent, with_rules=args.with_rules,
            force=args.force, dry_run=args.dry_run, no_diff=args.no_diff,
        ))

    project_dir = Path(args.project_dir).resolve()
    sys.exit(bootstrap_project(
        project_dir=project_dir, project_name=args.project_name,
        with_rules=args.with_rules, force=args.force,
        upgrade=args.upgrade, dry_run=args.dry_run, no_diff=args.no_diff,
    ))


if __name__ == "__main__":
    main()
