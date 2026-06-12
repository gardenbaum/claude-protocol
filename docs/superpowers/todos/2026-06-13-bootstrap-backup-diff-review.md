# Review-Findings: bootstrap backup + diff feature

**Datum:** 2026-06-13
**Scope:** Commits `15ce175..5fb4537` (17 Commits, +1698/-145 Zeilen in 5 Dateien)
**Reviewer:** 4 parallele sub-agents (quality, resilience, test-coverage, convention) + correctness gestoppt, security ausstehend
**Methodik:** Multidimensionales Review, kein Fixes-Scope außer den Top-3 in dieser Session

---

## Status-Legende

- `[ ]` offen
- `[~]` in Arbeit
- `[x]` gefixt
- `[?]` disputed / braucht Entscheidung

---

## 🔴 CRITICAL — gefixt in dieser Session

### [x] TEST-1 — Geteilter Upgrade-Folder nie end-to-end getestet ✅
**Severity:** critical | **Priority:** P0 | **Aufwand:** ~15 Zeilen Test
**Fix in:** commit `e2dd174` — `test(bootstrap): assert recorder + cleanup_obsolete share the upgrade folder`

`bootstrap_project` ruft `cleanup_obsolete(timestamp=recorder.timestamp)` (Zeile 1136).
Wenn diese Verdrahtung beim nächsten Refactor bricht, fällt es niemandem auf.
Das war der zentrale Refactor-Vorteil (Commit `c18327a`).

**Fix:** Test der assertion macht, dass der recorder + cleanup_obsolete im
gleichen `.claude/.upgrades/<ts>/` Ordner landen.

---

## 🟠 HIGH — Backlog

### [x] RES-3 — bootstrap_project ohne top-level error handling ✅
**Severity:** high | **Priority:** P1 | **Aufwand:** ~10 Zeilen
**Fix in:** commit `d2cdc4c` — `fix(bootstrap): surface [CHANGES] report + save manifest on mid-step failure`

`bootstrap_project` (Zeile 1088-1167) hat kein try/except um die Sub-Steps
(install_beads, copy_agents, copy_hooks, copy_rules_and_skills,
copy_settings_and_claude_md). Wenn ein Sub-Step mid-run failed:
- Kein `recorder.print_report()` (User sieht Traceback, nicht den [CHANGES]-Block)
- Kein `save_manifest` (manifest veraltet, beim Re-Run alles als "modified"/"no_manifest")
- Backups existieren auf disk, sind aber unsichtbar

**Fix:** wrap body in `try/except Exception as e: recorder.print_report();
save_manifest(...)  # best-effort; return 1`.

### [x] RES-4 — _do_backup succeedet, _atomic_write failt → silent orphan ✅
**Severity:** high | **Priority:** P1 | **Aufwand:** ~3 Zeilen
**Fix in:** commit `9471b3f` — `fix(bootstrap): record put_file change before atomic_write`

`put_file` (Zeile 349-374): Reihenfolge ist backup → atomic_write → manifest update → changes.append.
Wenn atomic_write failt: backup ist auf disk, KEIN `changes` entry, KEIN report.

**Fix:** `changes.append(...)` VOR `_atomic_write`, sodass der Eintrag im
report erscheint. Label kann "failed" sein wenn atomic_write raised.

### [ ] RES-7 — cleanup_obsolete ohne per-target error handling
**Severity:** high | **Priority:** P1 | **Aufwand:** ~15 Zeilen

`cleanup_obsolete` (Zeile 621-692) ruft `_cleanup_file`/`_cleanup_dir`
unguarded. Mid-loop Failure (z.B. `shutil.rmtree` PermissionError auf
read-only target) → partial deletion, partial backup, partial manifest.

**Fix:** pro per-target Aufruf try/except, fehler in `report["failed"]`
sammeln, am Ende ausgeben.

### [ ] CONV-1 — Bug-fix 2cfa0d5 ohne regression test
**Severity:** high (TDD) | **Priority:** P1 | **Aufwand:** ~10 Zeilen

Commit `2cfa0d5` fixt macOS-symlink path resolution in `replace_tree`.
Hat keinen Test, der ohne den Fix failed hätte.

**Fix:** Test der mit einem symlink (oder macOS-spezifischem `/var` → `/private/var`)
den manifest key korrekt prüft.

### [ ] CONV-2 — Bug-fix c18327a ohne behavioral assertion
**Severity:** high (TDD) | **Priority:** P1 | **Aufwand:** ~5 Zeilen

Commit `c18327a` fixt dass `recorder.timestamp` zu `cleanup_obsolete` fließt.
Test-Change war nur mechanisch (signature), keine Assertion.

**Fix:** In `TestUpgradeFlag.test_upgrade_flag_calls_cleanup` den
`timestamp` kwarg der fake_cleanup capture'n und gegen recorder.timestamp prüfen.

### [ ] QUAL-1 — sha256 prefix + sentinel als magic strings
**Severity:** high (quality) | **Priority:** P2 | **Aufwand:** ~10 Zeilen

`"sha256:"` prefix in `bytes_sha256` (Zeile 170) und
`"sha256:legacy-auto-injected"` sentinel in `_auto_inject_legacy_files`
(Zeile 518) und in `cleanup_obsolete` (Zeile 661) als raw strings.
Sentinel könnte theoretisch mit echtem hash kollidieren.

**Fix:** Modul-level constants:
```python
HASH_PREFIX = "sha256:"
LEGACY_HASH_SENTINEL = f"{HASH_PREFIX}legacy-auto-injected"
```

### [ ] QUAL-2 — Action/subdir strings als magic strings
**Severity:** high (quality) | **Priority:** P2 | **Aufwand:** ~15 Zeilen

Action strings (`"new"`, `"overwritten"`, `"appended"`, `"removed"`,
`"unchanged"`) in `_classify`, `_record_tree_file`, `_summary_line` und Tests.
Subdir names (`"overwritten"`, `"obsolete"`) in `_ensure_backup_dir` (Zeile 257)
und `cleanup_obsolete` (Zeile 644).

**Fix:** Modul-level constants:
```python
ACTION_NEW = "new"; ACTION_OVERWRITTEN = "overwritten"
ACTION_APPENDED = "appended"; ACTION_REMOVED = "removed"
ACTION_UNCHANGED = "unchanged"
BACKUP_DIR_OVERWRITTEN = "overwritten"; BACKUP_DIR_OBSOLETE = "obsolete"
```

### [?] CONV-4 — Spec-vs-Implementation drift
**Severity:** low–high (decision needed) | **Priority:** P2

Spec (`docs/superpowers/specs/2026-06-12-bootstrap-backup-and-diff-design.md`)
erwähnt `remove_recorded` Helper + vereinheitlichten `print_report`.
Implementation hat das NICHT umgesetzt — `_print_cleanup_report` (Zeile 1058-1086)
läuft weiter separat, cleanup_obsolete Reports gehen NICHT durch recorder.

**Entscheidung:**
- (a) Implementation: `remove_recorded` auf ChangeRecorder, dann `_print_cleanup_report` löschen
- (b) Spec: aktualisieren, dass der aktuelle Split akzeptiert ist

**Frage an User.**

---

## 🟡 MEDIUM (priorisiert) — Backlog

### [ ] RES-8 — save_manifest ist nicht atomic
`save_manifest` (Zeile 194-200) nutzt `write_text` ohne temp+rename.
Mid-write kill → korrupte manifest → load_manifest returnt leer → alles
als "no_manifest" behandelt → full re-backup beim nächsten Run.

**Fix:** `_atomic_write` (oder free-function Version) für save_manifest.

### [ ] RES-5 — replace_tree resolve().relative_to fragility
MacOS-symlink edge case: `f.resolve().relative_to(self.project_dir / ".claude")`
kann ValueError werfen wenn ein File ein dangling symlink ist.
In Praxis safe (copytree default folgt symlinks), aber fragil.

**Fix:** defensive try/except um relative_to, oder `f.relative_to(...)` ohne
resolve (file ist garantiert unter dest_dir).

### [ ] TEST-2 bis TEST-6 — Branch/Integration gaps
- TEST-2: `--no-diff` end-to-end
- TEST-3: `replace_tree` skip-pfad (identische files)
- TEST-4: `print_report` mit mixed actions + "removed"
- TEST-5: `_label` "unmanaged" branch explicit
- TEST-6: `--force` end-to-end mit echten Sub-Steps

### [ ] TEST-7 bis TEST-12 — Resilience/edge-case
- TEST-7: `fh.write` failure in _atomic_write
- TEST-8: backup folder lazy-init
- TEST-9: copytree mid-way failure
- TEST-10: replace_tree mit leerem src
- TEST-11: _diff_lines für removed branch
- TEST-12: expliziter timestamp parameter

### [ ] QUAL-3, QUAL-4, QUAL-7 — DRY Verstöße
- QUAL-3: timestamp selection duplication (recorder + cleanup_obsolete)
- QUAL-4: `backup_fn` closure pattern ist 3x dupliziert
- QUAL-7: `manifest["files"]` indirection

### [ ] RES-1, RES-2, RES-6, RES-9 — kleinere resilience
- RES-1: unlink in _atomic_write cleanup kann selbst failen
- RES-2: fh.write / mkstemp failures untested
- RES-6: BOM/binary files in diff rendering
- RES-9: replace_tree recorded 'overwritten' bevor rmtree läuft (lügt wenn copy failt)

---

## 🟢 LOW — informational

- QUAL-5: `old_b`/`new_b` naming inconsistency
- QUAL-6: `".cp-tmp-"` prefix magic string
- CONV-3: borderline TDD in `b8786ae` (production + tests im gleichen commit)
- CONV-5, CONV-6, CONV-7: documentation, memory, simplify — alle ✓

---

## Top-3 Fixes in dieser Session

1. **TEST-1** ✅ commit `e2dd174` — geteilter Upgrade-Folder Test
2. **RES-3** ✅ commit `d2cdc4c` — top-level error handling in `bootstrap_project`
3. **RES-4** ✅ commit `9471b3f` — `changes.append` vor `_atomic_write` in `put_file`

Arbeitsweise: TDD, RED → GREEN → REFACTOR, je ein commit pro fix.
