# Design: Backup + Diff für jeden überschreibenden Schreibvorgang in `bootstrap.py`

**Datum:** 2026-06-12
**Status:** Design (Review-Gate)
**Scope:** `bootstrap.py`, `scripts/cli.js` (Hilfetext + Flag-Pass-through), `tests/test_bootstrap.py`

## Problem

`init`/`upgrade` überschreiben heute mehrere Dateien **ohne Backup und ohne sichtbaren Diff**:

1. `copy_hooks` — `.claude/hooks/*.cjs` werden immer überschrieben (kein Manifest-Gate, kein Backup).
2. `copy_rules_and_skills` — Skill `project-discovery/` per `rmtree` + `copytree` komplett ersetzt.
3. `copy_agents` / Rules — die Pfade „unchanged" und „forced" (`--force`) überschreiben ohne Backup.
4. `copy_settings_and_claude_md` — `settings.json` wird bei JSON-Parse-Fehler **komplett ersetzt**, ohne Backup.

Folge: Lokale, nicht in Git befindliche Änderungen (hand-editierte Hooks, `settings.json`, der Skill)
können bei einem Upgrade verloren gehen, ohne dass der Nutzer sieht, was sich geändert hat.

Bereits sicher (bleibt unverändert): user-modifizierte Agents/Rules ohne `--force` werden übersprungen
und die neue Version landet in `.claude/.upgrades/<rel>`; Obsolete-Dateien/-Verzeichnisse werden von
`cleanup_obsolete` bereits gesichert; CLAUDE.md/.gitignore werden nur angehängt (nicht-destruktiv).

## Ziele

- **Jeder** überschreibende oder löschende Schreibvorgang sichert die bestehende Datei zuerst byte-genau.
- Für **jede** inhaltlich geänderte Datei wird ein Diff erzeugt: immer eine kompakte Summary,
  standardmäßig zusätzlich der vollständige Unified-Diff (unterdrückbar via `--no-diff`).
- Greift bei **`init` und `upgrade`** gleichermaßen.
- `--force` ignoriert weiterhin den „user-modified"-Schutz, sichert aber **trotzdem** vorher.
- Jede Overwrite-Zeile ist mit `pristine` (= entspricht der zuletzt ausgelieferten Version laut Manifest)
  oder `locally-modified` (= weicht ab, also potenziell Nutzerarbeit) gelabelt.
- Schreibvorgänge sind **atomar** (Temp-Datei + `os.replace`), damit ein Crash mitten im Schreiben
  das Original nicht beschädigt.

## Nicht-Ziele

- Kein semantischer JSON-Diff für `settings.json` (Reformatierungs-Rauschen wird akzeptiert und in der
  Summary gekennzeichnet).
- Kein Anfassen der Zeilenenden: Inhalte werden byte-genau geschrieben (siehe „CRLF"-Hinweis unten).
- Keine Änderung der bestehenden „user-modified-skip → `.upgrades/<rel>`"-Logik für Agents/Rules.
- Kein Pruning alter `.upgrades/<ts>`-Ordner (bleiben gitignored; separate Aufgabe falls gewünscht).

## Architektur — `ChangeRecorder` (Approach A)

Ein Objekt kapselt Zustand und führt die geschützten Schreibvorgänge aus. Es absorbiert die heute einzeln
durchgereichten Parameter (`project_dir`, `manifest`, `force`, `dry_run`) plus die neuen (`no_diff`,
gemeinsamer Backup-Ordner), sodass die `copy_*`-Funktionen **weniger** Parameter haben (Metrik <5 bleibt).

```
class ChangeRecorder:
    def __init__(self, project_dir, manifest, *, force, dry_run, no_diff, timestamp): ...

    # Kernoperation: byte-genaues, atomares Schreiben mit Backup + Diff-Erfassung.
    def put_file(self, dest: Path, new_bytes: bytes, rel: str, *, backup: bool = True) -> str
        # 1. old = dest.read_bytes() if exists else None
        # 2. action: "new" (old is None) | "unchanged" (old == new) | "overwritten"/"appended"
        # 3. unchanged  -> nichts tun (kein Write, kein Backup, kein Diff)
        # 4. label: "pristine" wenn old-sha256 == manifest[rel] sonst "locally-modified"
        #           ("unmanaged" wenn rel nicht im Manifest, z. B. settings.json)
        # 5. backup=True & überschreibt -> old-bytes nach <backup_root>/overwritten/<rel> kopieren
        # 6. diff: beide Seiten best-effort dekodieren (errors="replace"); difflib.unified_diff
        # 7. atomar schreiben: NamedTemporaryFile im Zielordner -> os.replace(tmp, dest)
        # 8. manifest[rel] = sha256(new)  (Recorder besitzt das Manifest)
        # 9. change in self.changes anhängen; action zurückgeben

    def replace_tree(self, dest_dir, src_dir, rel_prefix) -> None
        # ganzes dest_dir byte-genau nach <backup_root>/overwritten/<rel_prefix> sichern,
        # pro Datei Diff (Backup <-> neue src) erfassen, dann bestehendes rmtree+copytree,
        # Manifest-Einträge unter rel_prefix neu setzen.

    def remove_recorded(self, rel, action="removed") -> None
        # nur Report-Eintrag (Backup hat cleanup_obsolete schon erledigt, gleicher backup_root)

    def print_report(self) -> None
        # Summary-Block + (falls not no_diff) volle Diffs
```

`backup_root` = `<project>/.claude/.upgrades/<timestamp>/` — **ein** Timestamp pro Bootstrap-Lauf,
geteilt mit `cleanup_obsolete`. Lazy angelegt (erst beim ersten echten Backup), in `dry_run` nie.

### CRLF / Byte-Treue (Blocker-Fix)

`SKILL.md` hat CRLF-Zeilenenden. `Path.read_text()/write_text()` würde sie auf macOS zu LF normalisieren
→ Inhalt + SHA ändern sich → Datei wäre dauerhaft „modified". Daher: **Schreiben und Backup immer über
Bytes** (`read_bytes`/`write_bytes`/`shutil.copy2`). Text wird **ausschließlich** zur Diff-Erzeugung
dekodiert (`errors="replace"`). Bei Dekodier-Fehler: Diff überspringen (`<binary, diff skipped>`),
Backup + Write trotzdem.

## Integration pro Schreibpfad

| Pfad | Änderung |
|------|----------|
| `copy_hooks` | `recorder.put_file(dest, hook_src.read_bytes(), "hooks/<name>")` statt `shutil.copy2` + manuellem Manifest-Set. Label via Manifest. |
| `copy_agents` / Rules | `should_update_file`-Gate **bleibt** (entscheidet skip vs. write). Bei `ok=True` (new/unchanged/forced) Write über `recorder.put_file(...)`. Bei `--force` auf user-modifizierter Datei → Recorder sichert sie vorher (der eigentliche Gewinn). „modified"-Skip-Pfad (`save_upgrade`) unverändert. |
| `copy_rules_and_skills` (Skill) | `recorder.replace_tree(dest, skill_src, "skills/project-discovery")` statt nacktem `rmtree`+`copytree`. |
| `copy_settings_and_claude_md` (settings.json) | Merge-Ergebnis bzw. Template als Bytes über `recorder.put_file(dest, ..., "settings.json")`. Schließt den Parse-Failure-Pfad (altes, kaputtes File wird gesichert). Summary kennzeichnet `(merged hooks)` / `(replaced — could not merge)`. |
| `copy_settings_and_claude_md` (CLAUDE.md, .gitignore) | Append-only: `recorder.put_file(dest, old+anhang, rel, backup=False)` → Diff ja, Backup nein, atomarer Write. |
| `cleanup_obsolete` | Bekommt den `recorder` (für gemeinsamen `backup_root`) und ruft `remove_recorded(...)` je entferntem Item. **Return-Dict-Struktur bleibt unverändert** (Tests hängen daran — nur intern teilen). Das bisherige `_print_cleanup_report` wird durch den vereinheitlichten `print_report` ersetzt. |

## Ausgabeformat

```
[CHANGES] 3 overwritten, 1 new, 1 appended, 0 removed   backup: .claude/.upgrades/20260612T191500Z
  overwritten  hooks/bash-guard.cjs            pristine          +12 -3
  overwritten  hooks/hook-utils.cjs            locally-modified  +1 -1
  overwritten  settings.json                   unmanaged (merged hooks)  +4 -0
  new          rules/resilience-standard.md
  appended     CLAUDE.md                       +28 -0

--- hooks/hook-utils.cjs   (backup: .upgrades/20260612T191500Z/overwritten/hooks/hook-utils.cjs)
+++ hooks/hook-utils.cjs   (new)
@@ ... @@
  ...unified diff...
```

`--no-diff` unterdrückt nur den vollen Diff-Block; Summary + Backups bleiben.
Summary kommt zuerst (greppbar), volle Diffs danach.

## CLI

- `bootstrap.py`: neues `--no-diff` (argparse), durchgereicht in `bootstrap_project` /
  `run_batch_upgrade` → `ChangeRecorder`.
- `scripts/cli.js`: nur Hilfetext ergänzen. `--no-diff` läuft durch den bestehenden Pass-through
  (kein Sonderhandling wie bei `--no-rules` nötig).

## Edge-Cases

- **Identischer Inhalt** → `unchanged`, kein Write/Backup/Diff (kein Rauschen auf frischem `init`).
- **`dry_run`** → Actions/Labels/Diffs werden berechnet und gedruckt, aber `backup_root` wird nie
  angelegt und nichts geschrieben.
- **Binär/nicht dekodierbar** → Backup + Write normal, Diff = `<binary, diff skipped>`.
- **Timestamp-Kollision** (zwei Läufe in derselben Sekunde) → teilen sich den Ordner; wie heute, akzeptabel.
- **Atomarität** → Backup zuerst, dann `os.replace(tmp, dest)`. Crash davor: Original intakt. Crash
  zwischen Backup und replace: Original intakt + Backup vorhanden. Kein Zustand mit beschädigtem Original.
- **Manifest** wird vom Recorder besessen und nach jedem Write aktualisiert; Label wird **vor** dem
  Manifest-Update aus dem alten SHA bestimmt.

## Tests (TDD, `tests/test_bootstrap.py`)

Neue Klasse `TestChangeRecorder` + Erweiterungen:

- `put_file`: new/unchanged/overwritten korrekt klassifiziert.
- Overwrite legt byte-genaues Backup unter `<ts>/overwritten/<rel>` an.
- Label: `pristine` wenn altes SHA == Manifest, sonst `locally-modified`, `unmanaged` ohne Eintrag.
- CRLF-Datei behält nach Write CRLF (Byte-Treue) — kein LF-Normalisieren.
- `--no-diff`: Summary + Backup vorhanden, voller Diff-Block fehlt.
- `settings.json` Parse-Failure: altes (kaputtes) File wird gesichert, neues geschrieben.
- Skill-Verzeichnis: geänderte `SKILL.md` wird gesichert + gediffed.
- `--force` auf user-modifizierter Datei: Backup wird trotzdem angelegt.
- `--dry-run`: nichts auf Platte (kein `backup_root`, keine Writes), aber Report enthält Diffs.
- Atomic write: kein `.tmp`-Rest nach Erfolg; bei simuliertem Write-Fehler bleibt Original unverändert.
- Regression: `cleanup_obsolete`-Return-Dict-Keys unverändert; `copy_settings_and_claude_md`-Call-Sites
  (Tests Z. 1257–1290) an neue Signatur angepasst.

## Verifikation (über Unit-Tests hinaus)

`python -m pytest tests/test_bootstrap.py -v` **und** ein echter End-to-End-Lauf in einem Wegwerf-Repo:
`init`, dann eine Hook-Datei hand-editieren, dann `upgrade` → prüfen, dass Backup + Diff erscheinen und
die Datei korrekt ersetzt wird; `upgrade --dry-run` → prüfen, dass nichts geschrieben wird.
