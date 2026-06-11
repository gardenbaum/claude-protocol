# Beads Workflow

## Beads = single source of truth. Nothing lives only in your head.

Context gets compacted. Sessions restart. Beads persist.

> **Reference (vendored, offline):** the current `bd` docs live in `docs/vendor/beads/`.
> Start at the relevance triage `docs/beads-reading-guide.md`, then open the specific page
> it points to. Refresh to latest upstream with `mise run docs-sync-beads`. For ad-hoc
> lookups, `bd <cmd> --help` is always authoritative.

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
- Full list of `--type` values (task, bug, feature, epic, spike, story, milestone, ...): `bd create --help`
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
   The worktree auto-detects the shared database via the common git directory (`git-common-dir`) — no `.beads/redirect` file is needed. All bd commands from inside the worktree operate on the single shared database from the main repo.

   **The `local` label is normal.** `bd worktree list` shows `local` for the worktree (and `shared` for `(main)`); `bd worktree info` shows `local (no redirect)`. The database is still shared — `bd list` from inside the worktree sees the shared tasks. Do NOT treat `local` as breakage. (`none` would mean a worktree with no beads at all.)

   **Bead state from the worktree is shared and synced automatically.** Mutations write to the one shared `.beads` Dolt database; the committed git hooks (`bd hooks install --shared`) sync it on `git push`/`pull`, and `.beads/issues.jsonl` travels in commits as a readable backup. You never manage export or sync by hand.
3. Claim the work (atomic assign + in_progress): `bd update {BEAD_ID} --claim`
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
3. `git push origin bd-{BEAD_ID}`
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

## Banned

- Working directly on main branch
- Implementing without BEAD_ID
- Merging your own branch (user merges via PR)
- Editing files outside your worktree
- Raw `git worktree add` — MUST use `bd worktree create`. Raw `git worktree add` creates a shadow `.beads/` copy, spawns orphan dolt-server processes, blocks file deletion, and loses bead data.
- To REMOVE a worktree, prefer `bd worktree remove <path>` — it runs safety checks (refuses on unpushed commits) and works on macOS/Linux. Use `--force` to skip checks. Raw `git worktree remove --force` + `git worktree prune` is a fallback only if `bd worktree remove` is unavailable on your platform.
