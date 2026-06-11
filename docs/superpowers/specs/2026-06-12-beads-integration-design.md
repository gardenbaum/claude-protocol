# Beads Integration Redesign — claude-protocol × bd 1.0.5

- **Date:** 2026-06-12
- **Status:** Approved (design); pending implementation plan
- **Target bd version:** 1.0.5 (Homebrew), docs vendored at upstream `33e71d2` (~1.0.6 unreleased)

## Problem

claude-protocol was built against `bd` behavior that has since moved upstream.
bd 1.0.5 now ships its own Claude Code integration, which **overlaps and collides**
with hand-rolled parts of claude-protocol, while the project's task data does **not**
reliably leave the local machine — contradicting the stated goal of a team /
multi-machine setup where beads are always synced and backed up in the git remote.

This spec corrects the integration to a clean division of ownership and adds an
automatic, no-per-machine-config sync + backup model.

## Verification methodology

Every claim below was checked against the **running bd 1.0.5 binary** (isolated
throwaway repos) and the **vendored docs** (`docs/vendor/beads/`), not against
assumptions. An initial automated drift scan produced several findings that did
**not** survive empirical checking — those reversals are recorded so the rationale
is auditable.

### Verified facts

| # | Fact (bd 1.0.5, empirically confirmed) |
|---|---|
| V1 | `bd init` **itself** registers `SessionStart → bd prime --hook-json` (matcher `""`, so it also fires on compaction) **and** writes a marked CLAUDE.md block (`<!-- BEGIN BEADS INTEGRATION v:1 … -->`). |
| V2 | `bd prime` outputs workflow rules, a session-close protocol, memory guidance, and a "Context Recovery: run `bd prime` after compaction" line. It **adapts to the git/Dolt remote** ("No git remote configured. Issues are saved locally only"). |
| V3 | Built-in statuses: `open, in_progress, blocked, deferred, closed, pinned, hooked`. `closed` is the status; `done` is a **category**, not a status. |
| V4 | Config defaults: `export.auto=false`, `export.git-add=false`, `export.path=issues.jsonl`, `dolt.auto-commit=on`, `dolt.auto-push` unset. |
| V5 | Worktree labels: `bd worktree list` shows `shared` for `(main)` and **`local`** for a linked worktree; `bd worktree info` shows **`local (no redirect)`**; `bd list` inside the worktree **sees the shared DB**. So a shared worktree is healthy at `local` — `none` would mean no beads at all. |
| V6 | `bd worktree remove` **works on macOS** and enforces safety checks (refused on "unpushed commits"). The raw-git fallback skips those checks. No `u51`/Windows caveat exists in any vendored doc. |
| V7 | With `export.auto=true`+`export.git-add=true`, mutating from inside a worktree produces **no stray `/issues.jsonl`** at worktree or repo root; the export lands correctly in main `.beads/issues.jsonl`. The bug commit `f00521e` defended against is **fixed** in 1.0.5. |
| V8 | Sync is **not** auto-wired: `bd init` left `dolt.remote` unset even with an `origin` present. Cross-machine sync requires explicit setup. |
| V9 | `bd hooks install --shared` installs git hooks into a **committable** `.beads-hooks/`: `pre-commit→Dolt commit`, `post-merge→Dolt sync`, `pre-push→Dolt sync`, `post-checkout`, `prepare-commit-msg` (agent identity). `--shared` = wired once, every clone gets it. |
| V10 | `bd dolt remote add origin <url>` wires the Dolt remote (stored under `refs/dolt/data`, can reuse the code's origin URL). `bd bootstrap` on a fresh clone detects `refs/dolt/data` on origin and clones the bead history. |

### Findings reversed by verification (recorded for audit)

- **Worktree labels were NOT a high-severity drift.** The rule's claim that
  `local (no redirect)` is normal and the DB is still shared is **correct** (V5).
  Only the wording "`bd worktree list` shows `none`" is wrong — it shows `local`.
  → downgraded to a low-severity wording fix.
- **`done` is not an "invented status" in a hook bug.** It is a real bd *category*;
  the canonical *status* is `closed`. The fix is terminology alignment, not a bug.
- **`export.git-add false` is already the default (V4).** The bootstrap call is a
  no-op on 1.0.5 and its comment ("defaults true") is wrong — and we now want it
  `true` anyway (V7), so the whole disable-and-guard mechanism is obsolete.
- **The "compaction re-prime gap" is already closed natively (V1/V2).** bd's own
  hook runs `bd prime` (incl. compaction recovery + memories). The real issue is
  **duplication**, not absence.

## Decisions

1. **Integration direction — A: bd-native owns session context.** bd's hook +
   `bd prime` own workflow context, memories, compaction recovery, and the generic
   CLAUDE.md beads block. claude-protocol keeps only its genuine differentiators:
   **enforcement hooks, dev rules, worktree-per-bead orchestration, agents, safe
   install/upgrade.** Delete duplication.
2. **Sync/backup — Both: Dolt sync + JSONL git-backup, automated.** Dolt remote =
   `origin` for reliable team sync (handles deletions/merges); `export.auto`+
   `export.git-add` keep a human-readable `.beads/issues.jsonl` traveling in normal
   commits as a git-remote backup. Automated via committed git hooks so the agent
   never has to "figure out" syncing.

## Design

### Guiding principle

> bd owns what bd now does natively. claude-protocol owns enforcement, dev rules,
> orchestration, and safe install. No content is duplicated across the two.

### 1. Session context & CLAUDE.md (Direction A)

- **Shrink `session-start.cjs`.** Remove the bead dashboard (in_progress / ready /
  blocked / stale, the "use beads for all tasks" text, the "no active beads" line) —
  `bd prime` provides this now. **Keep only what bd does not do:** dirty-main
  warning, "worktree merged but bead still open" ACTION REQUIRED + cleanup hint,
  open-PR reminder via `gh`. bd's `bd prime` hook and this trimmed hook coexist as
  two SessionStart entries with **non-overlapping content**.
- **Trim `templates/CLAUDE.md`.** Remove the generic beads repetition. Keep
  orchestrator identity, investigation-before-delegation, the worktree/epic/
  quick-fix workflow, and agents. Point to `bd prime` for bd basics.
- **Deterministic reconcile in bootstrap.** Detect bd's
  `<!-- BEGIN BEADS INTEGRATION -->` marker and append claude-protocol's
  orchestration section after it (instead of the current "contains 'beads' → skip"
  heuristic). The settings merge **must preserve** bd's `SessionStart → bd prime
  --hook-json` entry alongside claude-protocol's hooks.

### 2. Sync & backup (both, automatic)

In the bootstrap "Installing beads" step, after `bd init` (best-effort, never fails
the bootstrap — same discipline as today's `configure_beads_export`):

- `bd dolt remote add origin <origin-url>` when an `origin` remote exists.
- `bd hooks install --shared` → committable auto-sync hooks (pre-push pushes Dolt,
  post-merge pulls Dolt). The agent's normal `git push`/`git pull` carries beads.
- `bd config set export.auto true` and `bd config set export.git-add true` → readable
  `.beads/issues.jsonl` backup committed alongside code.
- **`.gitignore`:** drop the obsolete `/issues.jsonl` guard (V7). Keep `.beads/`
  tracked; `.beads/dolt/` stays ignored via bd's own `.beads/.gitignore`.
- **Replace** `configure_beads_export()` (which disabled export) with the enable +
  remote + hooks setup. Fix/remove its stale comment.
- **Docs:** fresh clone / new machine → `bd bootstrap`.

### 3. Align `beads-workflow.md` to bd 1.0.5

- **C1** worktree labels: `bd worktree list` shows `local` (not `none`) for a
  worktree; `bd worktree info` shows `local (no redirect)`; DB is shared — keep the
  reassurance (correct), fix the label name.
- **C2** prefer `bd worktree remove` (has safety checks, works on macOS — V6); raw
  `git worktree remove` only as a sourced fallback. Defuse the unsourced "u51/Windows"
  claim (in rule text and `bash-guard.cjs`).
- **C5** use `closed` (status) not `done` (category); name the real status set.
- **C3/C4** where claude-protocol issues commands: `--json`, `bd update --claim`
  (start work), `bd close --reason "…"` (close).
- Version pin "bd 1.0.2+" → 1.0.5. Cut generic bd basics (now from `bd prime`);
  **keep** the claude-protocol protocol the enforcement hooks depend on (AWAITING
  REVIEW, completion report, epic-close, banned actions).

### 4. Hook hygiene

- **C8** `validate-completion.cjs` docstring "15 lines / 800 chars" → "25 / 1200"
  (match the enforced code); block message `bd comment` → `bd comments add`.
- **C5** `bash-guard.cjs` epic-close child filter: compare against `closed`
  (drop the phantom `done` status).

### 5. Docs

- `docs/decisions.md`: new section documenting the **reversal** (export.git-add
  false → bd-native + Dolt sync; why `f00521e`'s "JSONL is canonical" premise is
  obsolete under 1.0.5) and bd-native session ownership.
- `README.md`: update "What gets installed", the hooks table, the workflow, and the
  FAQ to reflect bd-native session priming + the sync model.

## What is deleted / kept / added

- **Deleted/shrunk:** bead-dashboard half of `session-start.cjs`; generic beads
  duplication in `templates/CLAUDE.md` and `beads-workflow.md`; `export.git-add false`
  disable + `/issues.jsonl` gitignore guard.
- **Kept (differentiators):** `enforce-branch-before-edit.cjs`, `bash-guard.cjs`,
  `validate-completion.cjs`; dev rules; `code-reviewer` / `merge-supervisor` agents;
  safe install/upgrade + manifest; `nudge-claude-md-update.cjs` (CLAUDE.md "Current
  State" — complementary, not bd-workflow); the trimmed `session-start.cjs`.
- **Added:** Dolt remote wiring + `bd hooks install --shared` + export enable in
  bootstrap; fresh-clone `bd bootstrap` doc; idiom fixes in the rule.

## Open items to confirm during implementation (not blockers)

1. Does the Dolt remote accept the raw git origin URL? Confirm with a real
   push/pull roundtrip against a bare remote.
2. Settings merge ordering: `bd init` (step 1) writes bd's SessionStart hook first;
   the later settings merge must array-append, not replace — verify and test.
3. What does bd's generated `.beads/.gitignore` cover (e.g. `interactions.jsonl`)?
   Ensure we commit `issues.jsonl` but not runtime churn.
4. `bd hooks install --shared` interaction with the worktree-per-bead model and any
   existing project git hooks.

## Sizing — epic with children

> 1 bead = 1 PR = 1 reviewable diff. This touches bootstrap, a rule, two hooks,
> settings semantics, .gitignore, the CLAUDE.md template, and docs → epic.

1. **Sync & backup** — bootstrap (Dolt remote + `bd hooks install --shared` +
   export enable), `.gitignore`, fresh-clone doc. *(core requirement)*
2. **De-duplicate session context** — shrink `session-start.cjs`; reconcile
   `templates/CLAUDE.md` + bootstrap CLAUDE.md logic; preserve bd's SessionStart hook
   in the merge.
3. **Align the rule** — `beads-workflow.md`: C1, C2, C3, C4, C5, version pin, trim.
4. **Hook hygiene** — `validate-completion.cjs` (C8), `bash-guard.cjs` (C5).
5. **Docs** — `decisions.md` reversal section, `README.md` updates.
6. **Verification & tests** — update `tests/test_bootstrap.py` (the existing
   `TestConfigureBeadsExport` changes meaning), Dolt push roundtrip, settings-merge
   test, hook unit tests.

## Acceptance criteria (epic)

- A fresh `npx … init` in a repo with an `origin` yields: bd's `bd prime` SessionStart
  hook **and** claude-protocol's enforcement hooks both present; a single,
  non-duplicated beads section in CLAUDE.md; `export.auto`/`git-add` on; a Dolt remote
  on origin; committed shared git hooks; `.beads/issues.jsonl` tracked.
- A second clone running `bd bootstrap` then `git pull` sees the same beads.
- No `/issues.jsonl` stray-guard remains; no `export.git-add false`.
- `beads-workflow.md` matches verified bd 1.0.5 behavior (V3, V5, V6).
- `npm test` and `python -m pytest tests/test_bootstrap.py -v` pass.
