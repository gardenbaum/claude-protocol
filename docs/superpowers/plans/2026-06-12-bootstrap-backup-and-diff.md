# Backup + Diff for Overwriting Writes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every overwriting/replacing write in `bootstrap.py` through a `ChangeRecorder` that makes a byte-exact backup, writes atomically, and prints a per-file diff (summary always, full unified diff unless `--no-diff`).

**Architecture:** A single `ChangeRecorder` object owns the per-run backup folder (`.claude/.upgrades/<ts>/overwritten/`), the manifest, and the collected change list. The `copy_*` functions stop writing directly and call `recorder.put_file(...)` / `recorder.replace_tree(...)`. `bootstrap_project` builds the recorder, threads it in, and prints the report. `cleanup_obsolete` (deletions) is left untouched — it already backs up and reports; the recorder covers only the previously-unguarded overwrite paths.

**Tech Stack:** Python 3 (stdlib: `difflib`, `tempfile`, `os`, `hashlib`, `shutil`), pytest. JS shim `scripts/cli.js` only gets a help-text line.

**Scope deviations from the committed spec (intentional, lower-risk):**
- Backups live under the recorder's own `.upgrades/<ts>/overwritten/`, separate from `cleanup_obsolete`'s existing `.upgrades/<ts>/obsolete/`. Not one shared timestamp.
- Report is two blocks: `[CHANGES]` (overwrites/new/appends) + the existing `[UPGRADE] Cleanup:` (deletions). Not one unified block.
- `.gitignore` (handled by `setup_gitignore`) is **not** routed through the recorder — it is append-only/non-destructive and already prints each added line. `CLAUDE.md` append **is** routed (diff, no backup) because we are already editing that function.

---

## File Structure

- **Modify** `bootstrap.py`:
  - Add imports `difflib`, `tempfile`.
  - Add `bytes_sha256()` helper next to `file_sha256()`.
  - Add `_render()` helper; make `copy_and_replace()` reuse it.
  - Add class `ChangeRecorder` (new section after the MANIFEST section).
  - Rewrite `copy_agents`, `copy_hooks`, `copy_rules_and_skills`, `copy_settings_and_claude_md` to take/use the recorder. Add helpers `_copy_rule`, `_merge_settings`, `_json_bytes`, `_write_settings`, `_write_claude_md`.
  - Wire `bootstrap_project`, `run_batch_upgrade`, `main` (new `--no-diff` arg).
- **Modify** `tests/test_bootstrap.py`:
  - New class `TestChangeRecorder`.
  - Update the 3 tests that call `copy_settings_and_claude_md(tmp_path, "Proj")` directly.
- **Modify** `scripts/cli.js`: one help-text line for `--no-diff`.

All commands below run from the repo root `/Users/fabian/Documents/Git/GitHub/gardenbaum/claude-protocol`.

---

## Task 1: `bytes_sha256` + `ChangeRecorder.put_file` (core write/backup/diff)

**Files:**
- Modify: `bootstrap.py` (imports near line 20; helper near `file_sha256` ~line 160; new class after `save_upgrade` ~line 221)
- Test: `tests/test_bootstrap.py` (new class `TestChangeRecorder`, append near end of file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bootstrap.py`:

```python
# ============================================================================
# ChangeRecorder: backup + atomic write + diff
# ============================================================================

class TestChangeRecorder:
    def _rec(self, tmp_path, **kw):
        return bootstrap.ChangeRecorder(tmp_path, {"files": {}}, **kw)

    def test_new_file_written_no_backup(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        action = rec.put_file(dest, b"hello\n", "hooks/x.cjs")
        assert action == "new"
        assert dest.read_bytes() == b"hello\n"
        assert not (rec.backup_root / "overwritten").exists()

    def test_unchanged_is_noop(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / "f.txt"
        dest.write_bytes(b"same\n")
        assert rec.put_file(dest, b"same\n", "f.txt") == "unchanged"
        assert not (rec.backup_root).exists()

    def test_overwrite_backs_up_byte_exact(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old\r\nline\r\n")  # CRLF must be preserved in backup
        rec.put_file(dest, b"new\n", "hooks/x.cjs")
        backup = rec.backup_root / "overwritten" / ".claude" / "hooks" / "x.cjs"
        assert backup.read_bytes() == b"old\r\nline\r\n"
        assert dest.read_bytes() == b"new\n"

    def test_label_pristine_vs_locally_modified(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "rules" / "r.md"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"shipped\n")
        rec.manifest["files"]["rules/r.md"] = bootstrap.bytes_sha256(b"shipped\n")
        rec.put_file(dest, b"v2\n", "rules/r.md")
        assert rec.changes[-1]["label"] == "pristine"

        dest.write_bytes(b"user edit\n")
        rec.put_file(dest, b"v3\n", "rules/r.md")
        assert rec.changes[-1]["label"] == "locally-modified"

    def test_dry_run_writes_nothing(self, tmp_path):
        rec = self._rec(tmp_path, dry_run=True)
        dest = tmp_path / "f.txt"
        dest.write_bytes(b"orig\n")
        rec.put_file(dest, b"changed\n", "f.txt")
        assert dest.read_bytes() == b"orig\n"
        assert not rec.backup_root.exists()
        assert rec.changes[-1]["action"] == "overwritten"  # still recorded for the report

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "a.txt"
        rec.put_file(dest, b"data\n", "a.txt")
        leftovers = [p.name for p in dest.parent.iterdir() if p.name.startswith(".cp-tmp-")]
        assert leftovers == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -v`
Expected: FAIL — `AttributeError: module 'bootstrap' has no attribute 'ChangeRecorder'`.

- [ ] **Step 3: Add imports**

In `bootstrap.py`, the import block (around lines 20-27) currently is:

```python
import os
import sys
import json
import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
```

Add `difflib` and `tempfile`:

```python
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
```

- [ ] **Step 4: Add `bytes_sha256` helper**

In `bootstrap.py`, right after `file_sha256` (ends ~line 164), add:

```python
def bytes_sha256(data: bytes) -> str:
    """Return hex SHA-256 digest of raw bytes (same scheme as file_sha256)."""
    h = hashlib.sha256()
    h.update(data)
    return f"sha256:{h.hexdigest()}"
```

- [ ] **Step 5: Add the `ChangeRecorder` class (core)**

In `bootstrap.py`, after `save_upgrade` (ends ~line 220) and before the `# UPGRADE CLEANUP` section, add. `_upgrade_timestamp()` is defined just below in that section — it is module-level, so referencing it here is fine at call time.

```python
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
        action = "new" if old_bytes is None else ("overwritten" if backup else "appended")
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): add ChangeRecorder with byte-exact backup + atomic write"
```

---

## Task 2: `ChangeRecorder.replace_tree` (skill directory)

**Files:**
- Modify: `bootstrap.py` (add methods to `ChangeRecorder`)
- Test: `tests/test_bootstrap.py` (add methods to `TestChangeRecorder`)

- [ ] **Step 1: Write the failing tests**

Add to class `TestChangeRecorder`:

```python
    def test_replace_tree_backs_up_changed_file(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_bytes(b"old skill\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"new skill\n")

        rec.replace_tree(dest, src, "skills/project-discovery")

        assert (dest / "SKILL.md").read_bytes() == b"new skill\n"
        backup = (rec.backup_root / "overwritten" / ".claude" / "skills"
                  / "project-discovery" / "SKILL.md")
        assert backup.read_bytes() == b"old skill\n"
        actions = {c["key"]: c["action"] for c in rec.changes}
        assert actions["skills/project-discovery/SKILL.md"] == "overwritten"

    def test_replace_tree_records_new_when_no_dest(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"fresh\n")
        rec.replace_tree(dest, src, "skills/project-discovery")
        assert (dest / "SKILL.md").read_bytes() == b"fresh\n"
        assert rec.changes[-1]["action"] == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder::test_replace_tree_backs_up_changed_file -v`
Expected: FAIL — `AttributeError: 'ChangeRecorder' object has no attribute 'replace_tree'`.

- [ ] **Step 3: Add `replace_tree` + helpers**

Add to `ChangeRecorder`:

```python
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
        for f in dest_dir.rglob("*"):
            if f.is_file():
                key = str(f.relative_to(self.project_dir / ".claude")).replace("\\", "/")
                self.manifest["files"][key] = file_sha256(f)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): ChangeRecorder.replace_tree for the skill directory"
```

---

## Task 3: `ChangeRecorder.print_report` (summary + diffs, `--no-diff`)

**Files:**
- Modify: `bootstrap.py` (add methods to `ChangeRecorder`)
- Test: `tests/test_bootstrap.py` (add methods to `TestChangeRecorder`, uses `capsys`)

- [ ] **Step 1: Write the failing tests**

Add to class `TestChangeRecorder`:

```python
    def test_report_shows_summary_and_diff(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"line1\nline2\n")
        rec.put_file(dest, b"line1\nCHANGED\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "[CHANGES]" in out
        assert "overwritten" in out
        assert "hooks/x.cjs" in out
        assert "-line2" in out and "+CHANGED" in out  # full diff present

    def test_report_no_diff_suppresses_full_diff(self, tmp_path, capsys):
        rec = self._rec(tmp_path, no_diff=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"line1\nline2\n")
        rec.put_file(dest, b"line1\nCHANGED\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "hooks/x.cjs" in out          # summary still shown
        assert "+CHANGED" not in out          # full diff suppressed

    def test_report_empty_says_no_changes(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        rec.print_report()
        out = capsys.readouterr().out
        assert "[CHANGES]" in out
        assert "no changes" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder::test_report_shows_summary_and_diff -v`
Expected: FAIL — `AttributeError: 'ChangeRecorder' object has no attribute 'print_report'`.

- [ ] **Step 3: Add `print_report` + helper**

Add to `ChangeRecorder`:

```python
    @staticmethod
    def _summary_line(c):
        label = f"  {c['label']}" if c.get("label") else ""
        counts = "" if c["action"] == "new" else f"  +{c['added']} -{c['removed']}"
        return f"  {c['action']:<12} {c['key']}{label}{counts}"

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
            for c in changed:
                if c["diff"]:
                    print("")
                    for line in c["diff"]:
                        print(line)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): ChangeRecorder.print_report (summary + diff, --no-diff)"
```

---

## Task 4: Integrate `copy_hooks` + `copy_agents` (+ `_render` refactor)

**Files:**
- Modify: `bootstrap.py` (`copy_and_replace` ~148, `copy_agents` ~686, `copy_hooks` ~718)

No new behavioral test here — these functions have no direct unit tests, and the `bootstrap_project` tests monkeypatch them with `lambda *a, **kw`, so signature changes are safe. End-to-end coverage comes in Task 9. This task is a pure refactor verified by the full suite still passing.

- [ ] **Step 1: Add `_render`, make `copy_and_replace` reuse it**

Replace the current `copy_and_replace` (lines ~148-153):

```python
def copy_and_replace(source: Path, dest: Path, replacements: dict) -> None:
    content = source.read_text(encoding='utf-8')
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding='utf-8')
```

with:

```python
def _render(source: Path, replacements: dict) -> str:
    content = source.read_text(encoding='utf-8')
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def copy_and_replace(source: Path, dest: Path, replacements: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render(source, replacements), encoding='utf-8')
```

- [ ] **Step 2: Rewrite `copy_hooks`**

Replace the current `copy_hooks` (lines ~718-730) with:

```python
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
```

- [ ] **Step 3: Rewrite `copy_agents`**

Replace the current `copy_agents` (lines ~686-715) with:

```python
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
```

- [ ] **Step 4: Run the full suite (expect failures only in bootstrap_project wiring, fixed in Task 7)**

Run: `python -m pytest tests/test_bootstrap.py -q`
Expected at this point: `TestChangeRecorder`, `TestCopyAndReplace` PASS. Some failures are expected because `bootstrap_project` still calls the old signatures — those get fixed in Task 7. Confirm there are **no** errors mentioning `_render` or `copy_and_replace`.

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py
git commit -m "refactor(bootstrap): route copy_hooks + copy_agents through ChangeRecorder"
```

---

## Task 5: Integrate `copy_rules_and_skills`

**Files:**
- Modify: `bootstrap.py` (`copy_rules_and_skills` ~733-794, add helper `_copy_rule`)

- [ ] **Step 1: Rewrite `copy_rules_and_skills` and add `_copy_rule`**

Replace the current `copy_rules_and_skills` (lines ~733-794) with:

```python
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
```

- [ ] **Step 2: Run TestChangeRecorder + smoke-import to confirm no syntax errors**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -q && python -c "import bootstrap"`
Expected: PASS + clean import (no output from the import).

- [ ] **Step 3: Commit**

```bash
git add bootstrap.py
git commit -m "refactor(bootstrap): route copy_rules_and_skills through ChangeRecorder"
```

---

## Task 6: Integrate `copy_settings_and_claude_md` + update its tests

**Files:**
- Modify: `bootstrap.py` (`copy_settings_and_claude_md` ~797-851, add `_merge_settings`, `_json_bytes`, `_write_settings`, `_write_claude_md`)
- Modify: `tests/test_bootstrap.py` (3 tests calling `copy_settings_and_claude_md` directly: lines ~1257, ~1273-1274, ~1289-1290)

- [ ] **Step 1: Update the existing tests to the new signature**

In `tests/test_bootstrap.py`, change `bootstrap.copy_settings_and_claude_md(tmp_path, "Proj")` to build a recorder first.

In `test_bd_prime_hook_survives_merge` (line ~1257), replace:

```python
        bootstrap.copy_settings_and_claude_md(tmp_path, "Proj")
```

with:

```python
        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")
```

In `test_orchestration_appended_once` (lines ~1273-1274), replace:

```python
        bootstrap.copy_settings_and_claude_md(tmp_path, "Proj")
        bootstrap.copy_settings_and_claude_md(tmp_path, "Proj")
```

with:

```python
        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")
        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")
```

In `test_create_path_idempotent` (lines ~1289-1290), make the identical replacement as in `test_orchestration_appended_once`.

- [ ] **Step 2: Run those tests to verify they now FAIL on the signature (red)**

Run: `python -m pytest tests/test_bootstrap.py::TestSettingsMergePreservesBdHook tests/test_bootstrap.py::TestClaudeMdAppendIdempotent -v`
Expected: FAIL — `TypeError: copy_settings_and_claude_md() takes ... ` (old signature still expects `project_dir, project_name`).

- [ ] **Step 3: Rewrite `copy_settings_and_claude_md` and add helpers**

Replace the current `copy_settings_and_claude_md` (lines ~797-851) with:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bootstrap.py::TestSettingsMergePreservesBdHook tests/test_bootstrap.py::TestClaudeMdAppendIdempotent -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "refactor(bootstrap): route settings.json + CLAUDE.md through ChangeRecorder"
```

---

## Task 7: Wire `bootstrap_project`, `run_batch_upgrade`, `main` (`--no-diff`)

**Files:**
- Modify: `bootstrap.py` (`bootstrap_project` ~928, `run_batch_upgrade` ~1009, `main` ~1047)

- [ ] **Step 1: Update `bootstrap_project` signature + body**

Change the signature (line ~928-931) to add `no_diff`:

```python
def bootstrap_project(
    project_dir: Path, project_name: str | None, with_rules: bool,
    force: bool, upgrade: bool, dry_run: bool, no_diff: bool = False,
) -> int:
```

Replace the block that loads the manifest and calls the copy steps (currently lines ~948-960):

```python
    manifest = load_manifest(project_dir)
    all_skipped = []

    if not install_beads(project_dir):
        return 1

    all_skipped += copy_agents(project_dir, resolved_name, manifest, force)
    copy_hooks(project_dir, manifest)
    all_skipped += copy_rules_and_skills(
        project_dir, with_rules, manifest, force,
    )
    copy_settings_and_claude_md(project_dir, resolved_name)
    setup_gitignore(project_dir)
```

with:

```python
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
```

The existing `if upgrade: report = cleanup_obsolete(...); _print_cleanup_report(...)` block and the `save_manifest` block stay exactly as they are (cleanup is untouched; `manifest` is the same object the recorder mutated).

- [ ] **Step 2: Update `run_batch_upgrade` to thread `no_diff`**

Change its signature (line ~1009-1011):

```python
def run_batch_upgrade(
    parent_dir: Path, with_rules: bool, force: bool, dry_run: bool, no_diff: bool = False,
) -> int:
```

And in its `bootstrap_project(...)` call (line ~1028-1031), add `no_diff=no_diff`:

```python
            rc = bootstrap_project(
                project_dir=child, project_name=None, with_rules=with_rules,
                force=force, upgrade=True, dry_run=dry_run, no_diff=no_diff,
            )
```

- [ ] **Step 3: Update `main` — add `--no-diff`, thread it through both call paths**

After the `--dry-run` argument (line ~1056), add:

```python
    parser.add_argument("--no-diff", action="store_true", help="Suppress full per-file diffs (summary + backups still shown)")
```

Update the `--all` branch call (line ~1062-1065):

```python
        sys.exit(run_batch_upgrade(
            parent_dir=parent, with_rules=args.with_rules,
            force=args.force, dry_run=args.dry_run, no_diff=args.no_diff,
        ))
```

Update the single-project call (line ~1068-1072):

```python
    sys.exit(bootstrap_project(
        project_dir=project_dir, project_name=args.project_name,
        with_rules=args.with_rules, force=args.force,
        upgrade=args.upgrade, dry_run=args.dry_run, no_diff=args.no_diff,
    ))
```

- [ ] **Step 4: Run the FULL suite**

Run: `python -m pytest tests/test_bootstrap.py -q`
Expected: all pass (existing `TestUpgradeFlag` tolerate the new recorder because they stub the copy_* and cleanup; `TestChangeRecorder` passes; settings/CLAUDE.md tests pass).

If `TestUpgradeFlag` fails because `recorder.print_report()` referenced a missing attribute, re-check Task 3. No changes to the cleanup fakes should be necessary.

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py
git commit -m "feat(bootstrap): wire ChangeRecorder + --no-diff into init/upgrade flow"
```

---

## Task 8: `scripts/cli.js` help text for `--no-diff`

**Files:**
- Modify: `scripts/cli.js` (Options block, lines ~23-29)

`--no-diff` already passes straight through `normalizeRulesFlag` (which only touches `--no-rules`/`--with-rules`), so only the help text needs updating.

- [ ] **Step 1: Add the option to the help text**

In the `Options:` block of `showHelp()`, after the `--dry-run` line, add a `--no-diff` line:

```
  --dry-run        Preview changes without writing (upgrade only)
  --no-diff        Suppress full per-file diffs (summary + backups still shown)
  --all <parent>   Batch upgrade: iterate subdirs of <parent> with .beads/ (upgrade only)
```

- [ ] **Step 2: Verify the CLI help renders and the flag passes through**

Run: `node scripts/cli.js help | grep -A1 no-diff`
Expected: the `--no-diff` line prints.

Run: `node scripts/cli.js upgrade --no-diff --dry-run --project-dir /tmp/cp-nope 2>&1 | head -5`
Expected: it invokes `bootstrap.py` (you'll see the "Bootstrapping..." banner or a templates/dir error) — confirming the flag did not cause an "Unknown command"/argparse error.

- [ ] **Step 3: Commit**

```bash
git add scripts/cli.js
git commit -m "docs(cli): document --no-diff flag"
```

---

## Task 9: Full verification (suite + real end-to-end)

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python + JS suites**

Run: `python -m pytest tests/test_bootstrap.py -v`
Expected: all pass.

Run: `npm test`
Expected: vitest passes (unchanged JS behavior).

- [ ] **Step 2: Real end-to-end — overwrite path produces backup + diff**

This exercises the actual copy_* path without the heavy `install_beads`/Dolt step by driving the recorder + copy functions directly against a temp project that already has a `.claude/` install.

Run:

```bash
python - <<'PY'
import json, tempfile, shutil
from pathlib import Path
import bootstrap

tmp = Path(tempfile.mkdtemp(prefix="cp-e2e-"))
# First install (fresh): everything is "new", no backups expected.
rec1 = bootstrap.ChangeRecorder(tmp, bootstrap.load_manifest(tmp))
bootstrap.copy_hooks(rec1)
bootstrap.copy_agents(rec1, "Demo")
bootstrap.copy_rules_and_skills(rec1, True)
bootstrap.save_manifest(tmp, rec1.manifest)
assert not (rec1.backup_root / "overwritten").exists(), "fresh install should not back up"

# Hand-edit a hook (simulates a local, uncommitted change).
hook = tmp / ".claude" / "hooks" / "bash-guard.cjs"
hook.write_bytes(hook.read_bytes() + b"\n// LOCAL EDIT\n")

# Second run: the edited hook must be backed up byte-exact and diffed.
manifest = bootstrap.load_manifest(tmp)
rec2 = bootstrap.ChangeRecorder(tmp, manifest, force=True)
bootstrap.copy_hooks(rec2)
rec2.print_report()
backup = rec2.backup_root / "overwritten" / ".claude" / "hooks" / "bash-guard.cjs"
assert backup.exists(), "edited hook must be backed up"
assert b"// LOCAL EDIT" in backup.read_bytes(), "backup must contain the local edit"
print("\nE2E OK — backup at:", backup)
shutil.rmtree(tmp)
PY
```

Expected: a `[CHANGES]` block listing `overwritten  hooks/bash-guard.cjs  locally-modified  +.. -..` followed by a unified diff, and `E2E OK — backup at: ...`. No assertion errors.

- [ ] **Step 3: Real end-to-end — `--no-diff` and dry-run via the CLI**

Run:

```bash
python - <<'PY'
import tempfile, shutil
from pathlib import Path
import bootstrap

tmp = Path(tempfile.mkdtemp(prefix="cp-e2e2-"))
rec = bootstrap.ChangeRecorder(tmp, bootstrap.load_manifest(tmp))
bootstrap.copy_hooks(rec); bootstrap.save_manifest(tmp, rec.manifest)
(tmp/".claude"/"hooks"/"bash-guard.cjs").write_bytes(b"changed\n")

# dry-run: nothing written, but report still computed
m = bootstrap.load_manifest(tmp)
dr = bootstrap.ChangeRecorder(tmp, m, force=True, dry_run=True, no_diff=True)
before = (tmp/".claude"/"hooks"/"bash-guard.cjs").read_bytes()
bootstrap.copy_hooks(dr); dr.print_report()
after = (tmp/".claude"/"hooks"/"bash-guard.cjs").read_bytes()
assert before == after, "dry-run must not write"
assert not dr.backup_root.exists(), "dry-run must not create backups"
print("\nDRY-RUN OK (no writes, no backup, summary shown, no full diff)")
shutil.rmtree(tmp)
PY
```

Expected: a `[DRY-RUN] [CHANGES]` summary with NO unified-diff lines, and `DRY-RUN OK ...`. No assertion errors.

- [ ] **Step 4: Self-review subagent (per implementation-standard)**

Dispatch a review of the diff (`git diff main~8..main -- bootstrap.py scripts/cli.js`) against the checklist in `.claude/rules/implementation-standard.md` (unhandled errors, metrics: function <30 lines / class <200 / nesting <4, duplication, conventions). Address any finding before declaring done.

- [ ] **Step 5: Final commit (if the self-review produced fixes)**

```bash
git add -A
git commit -m "test(bootstrap): verify backup + diff end-to-end; address self-review"
```

---

## Self-Review (plan vs. spec)

- **Spec coverage:** byte-exact backup → Task 1 (`_do_backup`, `_atomic_write`); atomic write → Task 1; pristine/locally-modified label → Task 1 (`_label`); diff summary + full diff + `--no-diff` → Task 3; init **and** upgrade → Task 7 (recorder built unconditionally); `--force` still backs up → Task 1 (backup is independent of `force`; verified in E2E Step 2); CRLF byte-treue → Task 1 test `test_overwrite_backs_up_byte_exact`; settings.json parse-failure backup → Task 6 (`_write_settings` except branch); skill via replace_tree → Task 2/5; CLAUDE.md append diff-no-backup → Task 6 (`backup=False`). Covered.
- **Deviations (flagged at top):** cleanup_obsolete untouched (deletions already backed up/reported); `.gitignore` not routed (non-destructive); two report blocks not one. None reduce safety.
- **Placeholder scan:** no TBD/TODO; every code step shows full code and exact run command + expected output.
- **Type/name consistency:** `ChangeRecorder`, `put_file`, `replace_tree`, `print_report`, `_render`, `_copy_rule`, `_merge_settings`, `_json_bytes`, `_write_settings`, `_write_claude_md`, `bytes_sha256` used identically across tasks. `put_file` returns one of new/unchanged/overwritten/appended consistently.
