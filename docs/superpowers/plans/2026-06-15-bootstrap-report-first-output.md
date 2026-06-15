# Report-first Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bootstrap.py`'s `init`/`upgrade` output answer "what changes if I run this?" from a single, consistent report instead of three overlapping, contradictory lists.

**Architecture:** The `ChangeRecorder` becomes the only source of change information. Skipped (user-modified) files are recorded via a new `kept` action. Copy steps stop printing per-file labels and instead print a one-line summary derived from the recorder delta. `print_report` is rewritten: verb mapping (UPDATE/NEW/APPEND/REMOVE), a note only for `locally-modified`, a `KEPT` section, and no `[CHANGES]`/`backup: (none)` noise. Internal `action`/`label` keys are unchanged.

**Tech Stack:** Python 3.12 stdlib (`difflib`, `pathlib`), pytest. Test file: `tests/test_bootstrap.py`.

**Reference spec:** `docs/superpowers/specs/2026-06-15-bootstrap-report-first-output-design.md`

---

### Task 1: `summarize_changes` helper (step-summary string)

A module-level pure function mapping a slice of `recorder.changes` to a one-line summary
like `"3 updated"` / `"1 updated · 2 kept"` / `"no changes"`.

**Files:**
- Modify: `bootstrap.py` (add module-level function near other print helpers, ~after `ChangeRecorder`)
- Test: `tests/test_bootstrap.py` (new test class `TestSummarizeChanges`)

- [ ] **Step 1: Write the failing test**

```python
class TestSummarizeChanges:
    def _c(self, action):
        return {"action": action, "key": "k", "label": None,
                "added": 0, "removed": 0, "diff": [], "backup": None}

    def test_empty_is_no_changes(self):
        assert bootstrap.summarize_changes([]) == "no changes"

    def test_counts_by_verb_in_order(self):
        slice_ = [self._c("new"), self._c("overwritten"), self._c("overwritten"),
                  self._c("appended"), self._c("kept"), self._c("kept")]
        assert bootstrap.summarize_changes(slice_) == "1 new · 2 updated · 1 appended · 2 kept"

    def test_only_updated(self):
        assert bootstrap.summarize_changes([self._c("overwritten")]) == "1 updated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap.py::TestSummarizeChanges -v`
Expected: FAIL with `AttributeError: module 'bootstrap' has no attribute 'summarize_changes'`

- [ ] **Step 3: Write minimal implementation**

Add after the `ChangeRecorder` class (after line ~405):

```python
_SUMMARY_VERB = {"new": "new", "overwritten": "updated", "appended": "appended",
                 "removed": "removed", "kept": "kept"}
_SUMMARY_ORDER = ["new", "updated", "appended", "removed", "kept"]


def summarize_changes(changes_slice) -> str:
    """One-line step summary from a slice of recorder.changes (verb-counted)."""
    tally = {}
    for c in changes_slice:
        verb = _SUMMARY_VERB.get(c["action"], c["action"])
        tally[verb] = tally.get(verb, 0) + 1
    if not tally:
        return "no changes"
    return " · ".join(f"{tally[v]} {v}" for v in _SUMMARY_ORDER if v in tally)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap.py::TestSummarizeChanges -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): summarize_changes helper for step summaries"
```

---

### Task 2: `ChangeRecorder.record_skip` (the `kept` action)

Records a user-modified, NOT-written file as a `kept` change so the report owns it.

**Files:**
- Modify: `bootstrap.py` (`ChangeRecorder`, add method near `put_file`, ~after line 377)
- Test: `tests/test_bootstrap.py` (`TestChangeRecorder`)

- [ ] **Step 1: Write the failing test**

Add inside `class TestChangeRecorder`:

```python
    def test_record_skip_adds_kept_change_no_write(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "rules" / "x.md"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"user edited\n")
        rec.record_skip("rules/x.md")
        assert dest.read_bytes() == b"user edited\n"          # untouched
        kept = rec.changes[-1]
        assert kept["action"] == "kept"
        assert kept["key"] == "rules/x.md"
        assert kept["label"] == "locally-modified"
        assert kept["backup"] is None
        assert not (rec.backup_root / "overwritten").exists()  # no backup dir
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder::test_record_skip_adds_kept_change_no_write -v`
Expected: FAIL with `AttributeError: 'ChangeRecorder' object has no attribute 'record_skip'`

- [ ] **Step 3: Write minimal implementation**

Add to `ChangeRecorder`, right after `put_file` (after line 377):

```python
    def record_skip(self, key):
        """Record a user-modified file that was NOT written (report-only)."""
        self.changes.append({"key": key, "action": "kept", "label": "locally-modified",
                             "added": 0, "removed": 0, "diff": [], "backup": None})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder::test_record_skip_adds_kept_change_no_write -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): record_skip records kept (user-modified) files in the report"
```

---

### Task 3: Rewrite `print_report` (headline, verbs, note, KEPT, empty state)

**Files:**
- Modify: `bootstrap.py` (`ChangeRecorder.print_report` + add `_VERB`, `_report_line`; remove `_summary_line`)
- Test: `tests/test_bootstrap.py` (update 4 format-bound tests, add 3 new)

- [ ] **Step 1: Update the format-bound tests + add new ones**

Replace the four existing report tests (`test_report_shows_summary_and_diff`,
`test_report_no_diff_suppresses_full_diff`, `test_report_empty_says_no_changes`,
`test_report_dry_run_prefix`, lines 1599–1639) with:

```python
    def test_report_shows_summary_and_diff(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"line1\nline2\n")
        rec.put_file(dest, b"line1\nCHANGED\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "1 file changed" in out
        assert "UPDATE" in out
        assert "hooks/x.cjs" in out
        assert "+1 -1" in out
        assert "\n-line2\n" in out and "\n+CHANGED\n" in out  # full diff present

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
        assert "No changes" in out

    def test_report_dry_run_prefix(self, tmp_path, capsys):
        rec = self._rec(tmp_path, dry_run=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"a\n")
        rec.put_file(dest, b"b\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "would change" in out
        assert "backup:" not in out  # no backup line in dry-run

    def test_report_kept_section_lists_user_modified(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        rec.record_skip("rules/beads-workflow.md")
        rec.record_skip("rules/debugging-standard.md")
        rec.print_report()
        out = capsys.readouterr().out
        assert "KEPT" in out
        assert "rules/beads-workflow.md" in out
        assert "rules/debugging-standard.md" in out
        assert "No changes" in out  # kept does not count as a change

    def test_report_note_only_on_locally_modified(self, tmp_path, capsys):
        rec = bootstrap.ChangeRecorder(
            tmp_path, {"files": {"hooks/x.cjs": "sha256:doesnotmatch"}}, force=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"user edited\n")
        rec.put_file(dest, b"new\n", "hooks/x.cjs")  # label -> locally-modified
        rec.print_report()
        out = capsys.readouterr().out
        assert "your edits will be replaced" in out

    def test_report_pristine_has_no_note(self, tmp_path, capsys):
        rec = bootstrap.ChangeRecorder(
            tmp_path, {"files": {"hooks/x.cjs": bootstrap.content_sha256("old\n")}})
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old\n")
        rec.put_file(dest, b"new\n", "hooks/x.cjs")  # label -> pristine
        rec.print_report()
        out = capsys.readouterr().out
        assert "your edits will be replaced" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -k report -v`
Expected: FAIL (new asserts like `"1 file changed"`, `"UPDATE"`, `"KEPT"` not yet produced)

- [ ] **Step 3: Rewrite `print_report` + helpers**

In `bootstrap.py`, replace `_summary_line` (lines 379–383) and `print_report` (lines 392–404)
with the following (keep `_print_diffs` unchanged):

```python
    _VERB = {"overwritten": "UPDATE", "new": "NEW", "appended": "APPEND", "removed": "REMOVE"}

    @classmethod
    def _report_line(cls, c, width):
        verb = cls._VERB.get(c["action"], c["action"].upper())
        note = "   your edits will be replaced" if c.get("label") == "locally-modified" else ""
        counts = "" if c["action"] == "new" else f"   +{c['added']} -{c['removed']}"
        return f"  {verb:<7} {c['key']:<{width}}{note}{counts}"

    def _headline(self, n):
        plural = "s" if n != 1 else ""
        if self.dry_run:
            return (f"DRY-RUN — {n} file{plural} would change, nothing written"
                    if n else "DRY-RUN — no changes, everything up to date")
        if not n:
            return "No changes — everything up to date"
        backup = f"   backup: {self.backup_root}" if self._backup_created else ""
        return f"{n} file{plural} changed{backup}"

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
            print("\n  KEPT (you modified these — new version staged in .claude/.upgrades/):")
            for c in kept:
                print(f"    {c['key']}")
        if changed:
            if self.dry_run:
                print("\n  Run without --dry-run to apply · --no-diff hides diffs")
            if not self.no_diff:
                print("\n  ── diffs ──")
                self._print_diffs(changed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bootstrap.py::TestChangeRecorder -v`
Expected: PASS (all, including the unchanged internal `action`/`label` tests)

- [ ] **Step 5: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): report-first print_report (verbs, KEPT, no backup-noise)"
```

---

### Task 4: Wire step functions + remove `all_skipped` plumbing and footer

Steps stop printing per-file lines; they print a one-line summary from the recorder delta.
Skipped files go through `record_skip`. The footer "skipped" block is removed.

**Files:**
- Modify: `bootstrap.py` — `copy_agents` (870–891), `copy_hooks` (894–903), `_copy_rule` (906–919),
  `copy_rules_and_skills` (922–944), `_write_settings` (967–985), `_write_claude_md` (988–1006),
  `copy_settings_and_claude_md` (1009–1014), `bootstrap_project` (1114–1156)
- Test: `tests/test_bootstrap.py` — `test_mid_step_failure_still_reports_and_saves_manifest` (1200–1250)

- [ ] **Step 1: Update the mid-step-failure test to the new headline**

In `test_mid_step_failure_still_reports_and_saves_manifest`, replace line 1244
(`assert "[CHANGES]" in out  # report still printed on failure`) with:

```python
        assert "2 files changed" in out  # report still printed on failure
```

(`agents/a.md` + `hooks/h.cjs` are both new files → 2 changed. The `assert "agents/a.md" in out`
on the next line stays.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest "tests/test_bootstrap.py::TestBootstrapProjectErrorHandling::test_mid_step_failure_still_reports_and_saves_manifest" -v`
Expected: FAIL (`"2 files changed"` not yet produced — old format prints `[CHANGES]`)

- [ ] **Step 3: Rewrite the step functions**

`copy_agents` (no return value, no per-file print):

```python
def copy_agents(recorder, project_name):
    """Copy code-reviewer and merge-supervisor templates."""
    print("\n[2/6] Agents", end="")
    start = len(recorder.changes)
    agents_dir = recorder.project_dir / ".claude" / "agents"
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
            save_upgrade(recorder.project_dir, rel_key, new_content)
            recorder.record_skip(rel_key)
    print(f" ... {summarize_changes(recorder.changes[start:])}")
```

`copy_hooks`:

```python
def copy_hooks(recorder):
    """Copy Node.js hooks (always overwrite — enforcement code), with backup + diff."""
    print("\n[3/6] Hooks", end="")
    start = len(recorder.changes)
    hooks_dir = recorder.project_dir / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for hook_file in (TEMPLATES_DIR / "hooks").glob("*.cjs"):
        dest = hooks_dir / hook_file.name
        recorder.put_file(dest, hook_file.read_bytes(), f"hooks/{hook_file.name}")
    print(f" ... {summarize_changes(recorder.changes[start:])}")
```

`_copy_rule` (no return value, no per-file print):

```python
def _copy_rule(recorder, rule_file, rules_dir):
    """Copy one rule verbatim through the recorder; record_skip if user-modified."""
    dest = rules_dir / rule_file.name
    rel_key = f"rules/{rule_file.name}"
    ok, _ = should_update_file(dest, rel_key, recorder.manifest, recorder.force)
    if ok:
        recorder.put_file(dest, rule_file.read_bytes(), rel_key)
    else:
        save_upgrade(recorder.project_dir, rel_key, rule_file.read_text(encoding="utf-8"))
        recorder.record_skip(rel_key)
```

`copy_rules_and_skills` (no return value, summary at end):

```python
def copy_rules_and_skills(recorder, with_rules):
    """Copy beads-workflow rule, project-discovery skill, and optional dev rules."""
    print("\n[4/6] Rules & skills", end="")
    start = len(recorder.changes)
    rules_dir = recorder.project_dir / ".claude" / "rules"
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
```

`_write_settings` — remove the three `print(...)` calls (lines 975, 982, 985). The function
body otherwise unchanged; it just calls `recorder.put_file(...)` without printing.

`_write_claude_md` — remove the three `print(...)` calls (lines 998, 1002, 1006). Keep the
early `return` on the "marker present" branch (line 1003's `return` stays; only its preceding
`print` is removed).

`copy_settings_and_claude_md`:

```python
def copy_settings_and_claude_md(recorder, project_name):
    """Write settings.json (merge hooks) and CLAUDE.md (append if exists)."""
    print("\n[5/6] Settings", end="")
    start = len(recorder.changes)
    _write_settings(recorder)
    _write_claude_md(recorder, project_name)
    print(f" ... {summarize_changes(recorder.changes[start:])}")
```

- [ ] **Step 4: Update `bootstrap_project` — drop `all_skipped` + footer**

In `bootstrap_project` (lines 1114–1156):
- Delete `all_skipped = []` (line 1114).
- Change `all_skipped += copy_agents(recorder, resolved_name)` → `copy_agents(recorder, resolved_name)` (line 1120).
- Change `all_skipped += copy_rules_and_skills(recorder, with_rules)` → `copy_rules_and_skills(recorder, with_rules)` (line 1122).
- Delete the footer block (lines 1152–1156):

```python
        if all_skipped:
            print(f"\n  {len(all_skipped)} file(s) skipped (user-modified):")
            for rel in all_skipped:
                print(f"    - {rel}")
                print(f"      Review: diff .claude/{rel} .claude/.upgrades/{rel}")
```

(The KEPT section in `print_report` now carries this information.)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/test_bootstrap.py -v`
Expected: PASS (all). If a step header in `[6/6]` setup_gitignore now reads oddly next to the
new terse steps, leave it — gitignore keeps its own informative lines per the spec.

- [ ] **Step 6: Commit**

```bash
git add bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): wire steps to recorder summaries; drop skipped-footer"
```

---

### Task 5: End-to-end dry-run verification (throwaway repo)

**NOT a code task.** Confirm the real CLI output matches the design. Per
`bootstrap-verification-hazard`: NEVER run a non-dry-run bootstrap against this repo's cwd
(`bd hooks install` hijacks `core.hooksPath`). Use a throwaway dir under `$HOME`.

- [ ] **Step 1: Fresh install into a throwaway repo**

```bash
SBX="$HOME/.cp-sbx-$$"; mkdir -p "$SBX" && git -C "$SBX" init -q
python bootstrap.py --project-dir "$SBX" --with-rules
```
Expected: `[2/6] Agents ... 2 new` (etc.), then a report headline `N files changed` with `NEW` rows.

- [ ] **Step 2: Hand-edit a hook, leave a rule pristine, then dry-run upgrade**

```bash
printf '\n// local edit\n' >> "$SBX/.claude/hooks/bash-guard.cjs"
python bootstrap.py --project-dir "$SBX" --with-rules --upgrade --dry-run
```
Expected:
- Step lines like `[3/6] Hooks ... 1 updated` / `[4/6] Rules & skills ... no changes`.
- Report headline `DRY-RUN — 1 file would change, nothing written` (or the real count).
- The edited hook row shows `UPDATE ... your edits will be replaced`.
- NO `backup:` line, NO `[CHANGES]` token, NO `(unchanged)` label anywhere.
- Nothing written: `git -C "$SBX" status` shows the manual edit only.

- [ ] **Step 3: Clean up**

```bash
rm -rf "$SBX"
```

- [ ] **Step 4: Final full-suite gate**

Run: `python -m pytest tests/test_bootstrap.py -v && npm test`
Expected: pytest + vitest all green.
