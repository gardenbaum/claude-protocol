# Beads Workflow

## Beads = single source of truth. Nothing lives only in your head.

Context gets compacted. Sessions restart. Beads persist.

### When to create a bead — ALWAYS if:
- User asks to implement, fix, refactor, or change anything
- You discover a bug, tech debt, or improvement during work
- A task needs follow-up that won't happen right now
- You start investigating something non-trivial

### After planning — size check then create beads:
When a plan is finalized and user confirms, BEFORE implementation:

**Step 1: Size check (one sentence decision):**
- >3 files OR >1 domain (DB + API, backend + frontend) → epic with children
- Description has "and then", "after that", multiple steps → multiple beads
- >50 lines estimated → consider splitting
- Otherwise → single bead

Rule of thumb: 1 bead = 1 PR = 1 reviewable diff.

**Step 2: Create beads:**
- Single task: `bd create "Task" -d "..."`
- Epic: `bd create "Feature" -d "..." --type epic`, then children with `--parent` and `--deps`
- Full list of `--type` values (built-in: `bug`, `feature`, `task`, `epic`, `chore`, `decision`; aliases: `enhancement`/`feat`→`feature`, `dec`/`adr`→`decision`; custom types require `types.custom` config): `bd create --help`
- Verify: `bd list` — the plan now lives in beads, not just in context

**Step 3: Only then start work** with `bd ready` → dispatch

### When NOT to create a bead:
- Quick fix approved by user (<10 lines, feature branch)
- Pure research/discussion with no code changes planned

### Status discipline:
- Created → `open` (default)
- Starting work → `bd update {ID} --claim` (atomic: assigns to you + sets `in_progress`)
- Submitted for review → `bd comments add {ID} "AWAITING REVIEW"` then leave bead at `in_progress` until user merges
- Merged → `bd close {ID} --reason "merged in PR #N"` (the status becomes `closed`; `done` is a category, not a status). Other built-in statuses: `open`, `in_progress`, `blocked`, `deferred`.
- **Epic status:** When starting work on the first child → `bd update {EPIC_ID} --status in_progress`. Epic stays `in_progress` until all children are done.
- **Never leave a bead in `in_progress` across sessions without reason**

### Discovered during work:
When you find tech debt, bugs, or improvements while working on something else:
```bash
bd create "Fix: [what]" -d "Discovered while working on {CURRENT_BEAD}: [details]"
```
Don't try to fix it now (unless trivial). Create the bead so it's not forgotten.

## Task Start

1. Parse BEAD_ID from dispatch prompt
2. Create worktree (MUST use bd, not raw git — see Banned):
   ```bash
   bd worktree create .worktrees/bd-{BEAD_ID} --branch bd-{BEAD_ID}
   cd .worktrees/bd-{BEAD_ID}
   ```
   In bd 1.0.2+ the worktree auto-detects the shared database via the common git directory (`git-common-dir`) — no `.beads/redirect` file is needed (it is obsolete). All bd commands from inside the worktree operate on the single shared database from the main repo.

   **Status labels `none` / `local (no redirect)` are normal.** `bd worktree list` shows `none` and `bd worktree info` shows `local (no redirect)` — these are cosmetic. The database is still shared (`bd list` from inside a fresh worktree sees the shared tasks). Do NOT treat `none`/`local` as a sign of breakage.

   **Protection against a stray `.beads/issues.jsonl` in the worktree** is `export.git-add false` plus `/issues.jsonl` in `.gitignore` (set up by bootstrap) — NOT a check of the worktree's shared status.
3. Mark in progress: `bd update {BEAD_ID} --status in_progress`
4. If this is a child of an epic — check epic status. If epic is still `open`, mark it too: `bd update {EPIC_ID} --status in_progress`
5. Read bead context: `bd show {BEAD_ID}` and `bd comments {BEAD_ID}`

## During Implementation

- Work ONLY in your worktree: `.worktrees/bd-{BEAD_ID}/`
- Commit frequently with descriptive messages
- Log progress: `bd comments add {BEAD_ID} "Completed X, working on Y"`

## Task Completion

Execute ALL steps in order:

1. **Self-verify against requirements:**
   - Run `bd show {BEAD_ID}` — re-read the description
   - Check every item/requirement from the description
   - If anything is missing — implement it now, don't skip
2. `git add -A && git commit -m "..."`
3. Push your branch **and** the bead database to origin:
   ```bash
   git push origin bd-{BEAD_ID}
   bd dolt push   # sync the shared bead DB to origin — REQUIRED so reviewers/teammates and fresh clones see your beads (auto-push alone is only eventual). If rejected, run `bd dolt pull` then `bd dolt push`.
   ```
4. Leave completion comment: `bd comments add {BEAD_ID} "Completed: [summary]"`
5. Signal review: `bd comments add {BEAD_ID} "AWAITING REVIEW"` — leave the bead at `in_progress`; the user closes it after merging the PR.
6. Return completion report (checklist is MANDATORY — hook will block without it):
   ```
   BEAD {BEAD_ID} COMPLETE
   Worktree: .worktrees/bd-{BEAD_ID}
   Checklist:
   - [x] requirement 1 from description
   - [x] requirement 2 from description
   Files: [names only]
   Tests: pass
   Summary: [1 sentence]
   ```

CLI: `bd prime` for the full workflow + command reference (auto-injected at session start), `bd <cmd> --help` for details. Prefer `--json` when parsing bd output programmatically.

## Beads v1.1.0 — features the workflow relies on

These were added or changed in Beads v1.1.0. The bootstrap and the workflow
expect them; the project may be on v1.0.x, in which case treat the affected
sections as soft guidance rather than hard rules.

- **`bd init --init-if-missing`** (idempotent) — bootstrap calls this when the
  installed `bd` supports it, so re-running `python bootstrap.py` on an
  already-initialized project is safe. No more "database already exists".
- **`bd prime` divergence reminder** — when `AGENTS.md` and `CLAUDE.md` drift
  out of sync (different orchestrator body, different bead section, different
  project description), `bd prime` prints a one-shot nudge at session start.
  Treat it as a TODO, not as a blocker: read both files, decide which is
  canonical, sync the other. The shared body lives in
  `templates/AGENTS.md` and `templates/CLAUDE.md` — diffing the
  installed `CLAUDE.md` against `AGENTS.md` is enough.
- **`bd metrics` with first-run consent** — `bd metrics` is the new
  observability surface (throughput, ready backlog, dep-graph health). The
  first run prints a one-line consent notice. **Do not run `bd metrics`
  without explicit user consent in this session** — the consent gate lives
  in the user, not in the workflow. If the user has already opted in once,
  subsequent runs are silent.
- **Sync repair cascade** — pull-time merge conflicts that used to strand
  the clone now auto-resolve (mixed-schema, dep-violation, drift). If a
  conflict still surfaces, the message is a recovery path, not a stack
  trace: read it, do what it says, then `bd doctor` to confirm clean state.
- **Compaction-archive-before-discard** — `bd compact` no longer loses data;
  it archives first and `bd restore` is wired in. If you (or the user)
  compacted before and the bead vanished, `bd restore {ID}` shows the
  archived content (read-only by default; pass `--apply` to actually
  write the original back and step the compaction level down).
- **`bd doctor` drift detection** — schema-migration content skew between
  the local DB and the cached remote ref is now flagged, not silent. The
  session-start hook surfaces any drift; if it does, `bd doctor` is the
  first command to run, not the last.

## Banned

- Working directly on main branch
- Implementing without BEAD_ID
- Merging your own branch (user merges via PR)
- Editing files outside your worktree
- Raw `git worktree add` — MUST use `bd worktree create`. Raw `git worktree add` creates a shadow `.beads/` copy, spawns orphan dolt-server processes, blocks file deletion, and loses bead data.
- For REMOVING a worktree, raw `git worktree remove --force` followed by `git worktree prune` IS allowed — `bd worktree remove` is broken on Windows (bug u51). (`bd worktree create` for creation still stands, because creation is what wires up the shared database.)
