# Design: Report-first Output für `init`/`upgrade` in `bootstrap.py`

**Datum:** 2026-06-15
**Status:** Design (Review-Gate)
**Scope:** `bootstrap.py` (Recorder-Anzeige + Step-Funktionen + Report), `tests/test_bootstrap.py`

## Problem

Seit v3.6.0 (ChangeRecorder) laufen **zwei** Reporting-Systeme nebeneinander, die
**dieselben** Schreibvorgänge beschreiben — mit unterschiedlichem Vokabular und teils
widersprüchlich:

1. **Die Pro-Schritt-Ausgabe** (`[1/6]…[6/6]`) labelt Dateien mit den `reason`-Werten aus
   `should_update_file` (`bootstrap.py:203`): `(unchanged)`, `(MODIFIED by user — skipped)`,
   oder gar nichts (Hooks: nackte Dateinamen, `bootstrap.py:902`).
2. **Der `[CHANGES]`-Report** (`print_report`, `bootstrap.py:392`) labelt dieselben Writes mit
   den ChangeRecorder-Actions `overwritten`/`new`/`appended` + `pristine`/`locally-modified`/
   `unmanaged` + Zeilen-Counts + Diffs.

Der konkrete Widerspruch aus einem echten Upgrade-Lauf: `rules/implementation-standard.md`
erscheint in `[4/6]` als `(unchanged)` und im `[CHANGES]`-Report als `overwritten … pristine
+19 -1`. Beides ist intern korrekt — `should_update_file` meint mit `"unchanged"` *„vom Nutzer
nicht verändert"* (= sicher überschreibbar), **nicht** *„Inhalt ändert sich nicht"*. Das
**Template** hat sich geändert, also wird die pristine Datei aktualisiert. Aber das Wort
`(unchanged)` liest sich als das Gegenteil.

Folge: Auf die Frage „was ändert sich, wenn ich das echt laufe?" gibt es drei überlappende,
teils widersprüchliche Listen (Pro-Schritt-Labels, `[CHANGES]`-Report, „skipped"-Footer), die
der Nutzer selbst abgleichen muss.

Bereits korrekt (bleibt die Wahrheitsquelle): der `[CHANGES]`-Report — **jeder** Write geht
durch `ChangeRecorder.put_file`/`replace_tree`. Die Pro-Schritt-Labels sind die unzuverlässige,
ältere Schicht.

## Ziele

- **Eine** Änderungs-Ansicht. Der `ChangeRecorder` ist die einzige Quelle dafür, was sich ändert.
- Die kopierenden Schritte (`[2/6]…[5/6]`) drucken **keine** Pro-Datei-Labels mehr, sondern eine
  Ein-Zeilen-Zusammenfassung, die aus dem Recorder-Delta dieses Schritts abgeleitet wird.
- Der Report beantwortet sofort: *welche* Dateien, *welche* Aktion (UPDATE/NEW/APPEND/REMOVE),
  *wie viele* Zeilen, und ob **lokale Edits** überschrieben werden.
- „Vom Nutzer editiert, daher übersprungen" (bisher `all_skipped` + Footer) wird als `KEPT`-Sektion
  **in den Report** verlegt — eine Liste statt drei.
- Das verwirrende `(unchanged)`-Wort und das im Dry-Run sinnlose `backup: (none)` verschwinden.

## Nicht-Ziele

- Keine Änderung der **internen** `ChangeRecorder`-API: `action`-Keys bleiben
  `new`/`unchanged`/`overwritten`/`appended`/`removed`; `_label` bleibt
  `pristine`/`locally-modified`/`unmanaged`. (Tests an `rec.changes[..]["action"]` bleiben grün.)
  Geändert wird nur die **Anzeige**.
- Keine Änderung der `should_update_file`-Logik (skip-vs-write-Entscheidung) — nur ihr `reason`
  wird nicht mehr roh gedruckt.
- Kein Rewrite von `cleanup_obsolete`/`_print_cleanup_report` — nur stilistische Angleichung der
  Dry-Run-Phrasierung.
- Keine Sprachumstellung: die Programmausgabe bleibt **Englisch** (README/Tool sind englisch).

## Architektur

### 1. „kept" als Recorder-Aktion (Single Source of Truth)

Übersprungene (user-modifizierte) Dateien werden künftig **im Recorder** erfasst statt über die
parallele `all_skipped`-Verkabelung:

```
def record_skip(self, key):
    # Datei wird NICHT geschrieben (User hat sie editiert). Nur Report-Eintrag.
    self.changes.append({"key": key, "action": "kept", "label": "locally-modified",
                         "added": 0, "removed": 0, "diff": [], "backup": None})
```

Damit fließt **alles** durch `recorder.changes`. `copy_agents` / `_copy_rule` rufen im skip-Pfad
`recorder.record_skip(rel_key)` statt eine Liste zurückzugeben und selbst zu drucken. Die
`all_skipped`-Rückgaben und der Footer-Block (`bootstrap.py:1152`) entfallen.

`put_file` legt `kept` nie an (es schreibt ja); `kept` entsteht ausschließlich über `record_skip`.

### 2. Step-Zusammenfassung aus dem Recorder-Delta

`put_file` hängt nur bei tatsächlicher Änderung an `self.changes` an (unchanged → früher Return,
`bootstrap.py:358`). Also ist `len(recorder.changes)` vor/nach einem Schritt = Zahl der in diesem
Schritt erfassten Änderungen. Ein kleiner Helfer fasst eine Slice zusammen:

```
def summarize(changes_slice) -> str:
    # zählt nach action; gibt z.B. "3 updated", "1 updated · 2 kept",
    # "1 updated · 1 appended", oder "no changes" zurück.
```

Jede kopierende Step-Funktion: `start = len(recorder.changes)` am Anfang, am Ende
`print(f"[n/6] <Name> … {summarize(recorder.changes[start:])}")`. Verb-Mapping wie im Report
(overwritten→updated, new→new, appended→appended, kept→kept, removed→removed).

### 3. `[1/6]` Beads und `[6/6]` gitignore bleiben aussagekräftig

Das sind **Status**-Schritte, keine Datei-Diffs (Install/Sync/Warnungen bzw. gitignore-Append über
rohes `open`). Ihre bestehenden Zeilen — insbesondere die **Warnung „existing git hooks detected"** —
bleiben sichtbar. Nur die vier Schreib-Schritte (`[2/6]…[5/6]`) werden terse.

### 4. `print_report` umgebaut

```
def print_report(self):
    changed = [c for c in self.changes if c["action"] not in ("unchanged", "kept")]
    kept    = [c for c in self.changes if c["action"] == "kept"]
    # Headline:
    #   dry_run:  "DRY-RUN — {n} file(s) would change, nothing written"
    #             ({n}==0 -> "DRY-RUN — no changes, everything up to date")
    #   real:     "{n} file(s) changed · backup: {backup_root}"  (oder "no changes …")
    # Pro changed-Zeile, Spalten an längster key-Breite ausgerichtet:
    #   "  {VERB:<7}{key:<W}{note}{counts}"
    #     VERB:   overwritten→UPDATE  new→NEW  appended→APPEND  removed→REMOVE
    #     note:   nur bei label=="locally-modified": "your edits will be replaced"
    #     counts: "" bei action=="new", sonst "+{added} -{removed}"
    # KEPT-Block (falls kept nicht leer):
    #   "  KEPT (you modified these — new version staged in .claude/.upgrades/):"
    #   "    {key}"  je Eintrag
    # Footer-Hint: "  Run without --dry-run to apply · --no-diff hides diffs"  (nur wenn changed)
    # Diffs (falls not no_diff): "  ── diffs ──" dann pro changed-Eintrag der unified diff
```

## Ausgabeformat (Zielzustand, echte Daten)

```
[1/6] Installing beads...
  - beads CLI already installed
  - WARNING: existing git hooks detected (core.hooksPath/.husky) — skipping ...
  - Sync configured (JSONL git-backup + Dolt remote + shared hooks)
  DONE
[2/6] Agents .......... no changes
[3/6] Hooks ........... 3 updated
[4/6] Rules & skills .. 1 updated · 2 kept (you modified them)
[5/6] Settings ........ 1 updated · 1 appended
[6/6] gitignore ....... already configured

DRY-RUN — 5 files would change, nothing written

  UPDATE  hooks/session-start.cjs         your edits will be replaced   +6 -48
  UPDATE  hooks/validate-completion.cjs                                 +2 -2
  UPDATE  hooks/bash-guard.cjs                                          +16 -1
  UPDATE  rules/implementation-standard.md                              +19 -1
  APPEND  CLAUDE.md                                                     +83

  KEPT (you modified these — new version staged in .claude/.upgrades/):
    rules/beads-workflow.md
    rules/debugging-standard.md

  Run without --dry-run to apply · --no-diff hides diffs
  ── diffs ──
  --- hooks/session-start.cjs
  +++ hooks/session-start.cjs
  @@ ... @@
  ...
```

## Integration pro Step-Funktion

| Funktion | Änderung |
|----------|----------|
| `copy_agents` | skip-Pfad → `recorder.record_skip(rel_key)` statt `skipped.append` + 2× `print`. write-Pfad → kein `print` pro Datei. Am Ende: Step-Summary-Zeile. Rückgabe `skipped` entfällt. |
| `copy_hooks` | kein `print` pro Hook (war ohnehin statuslos). Am Ende: Step-Summary-Zeile. |
| `_copy_rule` / `copy_rules_and_skills` | wie `copy_agents`: skip → `record_skip`, kein Pro-Datei-`print`, Step-Summary am Ende. Skill via `replace_tree` (unverändert), zählt in die Summary. Rückgabe `skipped` entfällt. |
| `copy_settings_and_claude_md` | `_write_settings`/`_write_claude_md` drucken keine Pro-Datei-Zeilen mehr; Step-Summary am Ende. (Die `(merged)`/`(replaced — could not merge)`-Unterscheidung bleibt als interne Logik; bei Bedarf als note im Report.) |
| `bootstrap_project` | `all_skipped`-Plumbing + Footer-„skipped"-Block (`:1152`) entfernt; `print_report` ist die einzige Änderungs-Ansicht. `record_skip` wird vor `print_report` gesammelt (passiert in den Step-Funktionen, also bereits davor). |

## Edge-Cases

- **Frischer `init`** (alles neu) → Report „N files would change" mit lauter `NEW`-Zeilen (keine
  Counts), kein KEPT-Block.
- **Nichts ändert sich** → „no changes, everything up to date" / „No changes". Kein KEPT, kein Hint.
- **`--force`** → user-modifizierte Dateien werden geschrieben (nicht kept); erscheinen als `UPDATE`
  mit note „your edits will be replaced". Kein KEPT-Block in diesem Fall.
- **Step-Summary bei nur kept** (z.B. alle Rules vom User editiert) → „0 updated · N kept" bzw.
  einfach „N kept".
- **Dry-Run** → Headline „DRY-RUN — …, nothing written", kein `backup:`-Suffix.

## Tests (TDD, `tests/test_bootstrap.py`)

Anzupassen (Format-gebunden):
- `test_report_shows_summary_and_diff` (~1605): assert auf `UPDATE`/Headline statt `[CHANGES]`/`overwritten`-Anzeige.
- `test_report_no_diff_suppresses_full_diff` (~1618): Diff-Block-Marker neu.
- `test_report_empty_says_no_changes` (~1625): „no changes"-Phrasierung neu.
- `test_report_dry_run_prefix` (~1636): `DRY-RUN —` statt `[DRY-RUN] [CHANGES]`; kein `backup: (none)`-Assert.
- Mid-Step-Failure-Test (~1244): assert auf neue Headline statt `[CHANGES]`.

Neu:
- `record_skip` hängt einen `kept`-Change an (keine Diffs/Counts, kein Backup, kein Write).
- `print_report` rendert die `KEPT`-Sektion mit den kept-keys.
- `summarize` liefert korrekte Strings für: nur updated / updated+kept / updated+appended / leer.
- `print_report` headline-Zählung ignoriert `unchanged` **und** `kept`.
- note „your edits will be replaced" erscheint nur bei `locally-modified`, nicht bei `pristine`/`unmanaged`.

Interne `action`/`label`-Asserts (Z. ~1469/1519/1533/1554/1577) bleiben **unverändert**.

## Verifikation (über Unit-Tests hinaus)

`python -m pytest tests/test_bootstrap.py -v` (alle grün) **und** ein echter End-to-End-Dry-Run in
einem **Wegwerf-Repo** (NIE gegen das cwd dieses Repos — `bd hooks install` würde `core.hooksPath`
kapern): `init` in `$tmp/proj`, dann eine Hook-Datei hand-editieren + eine Rule unverändert lassen,
dann `upgrade --dry-run` → prüfen, dass die neue Report-first-Ausgabe genau die geänderten Dateien,
die `KEPT`-Sektion und die Step-Summaries zeigt und nichts auf Platte schreibt.
