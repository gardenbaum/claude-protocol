# Beads — Reading Guide (Relevance Triage for claude-protocol)

Curated map of the vendored Beads docs (`docs/vendor/beads/`) for working on
**claude-protocol**: an orchestration toolkit whose `beads-workflow` rule prescribes how
Claude Code agents drive `bd` (worktree isolation, status discipline, epics/children,
`ready` → dispatch). You read these to keep that rule accurate against the real `bd`
behavior — not to learn Beads internals.

Paths below are relative to `docs/vendor/beads/`.

> **What is vendored:** Beads' curated docs site (`website/docs` upstream → `site/` here),
> plus two guides with no site equivalent (`guides/WORKTREES.md`,
> `guides/MULTI_REPO_MIGRATION.md`) and `CHANGELOG.md`. A scoped prune
> (`mise run docs-sync-beads`) keeps only the Claude-ecosystem integrations and drops the
> other agents (aider/cursor/codex/gemini/…). Beads' own dev-facing root files
> (`CLAUDE.md`/`AGENTS.md`/`AGENT_INSTRUCTIONS.md`) are **not** vendored — they document
> contributing to Beads itself, not using `bd`. Source commit: `UPSTREAM_COMMIT.txt`.

---

## 🟢 Core — read first (the foundation of the workflow rule)

- `guides/WORKTREES.md` — **most important**: shared `.beads` DB across worktrees, the
  difference between Beads-created (sync-branch) worktrees and user worktrees. This is the
  ground truth behind the rule's `bd worktree create` mechanics and the `none`/`local`
  status caveats.
- `site/getting-started/quickstart.md · installation.md` — the canonical happy path
- `site/core-concepts/issues.md` — the issue model (the unit the whole rule operates on)
- `site/core-concepts/sync-concepts.md` — how state syncs (Dolt); underpins "push after
  every mutation" and the AWAITING-REVIEW handoff
- `site/integrations/claude-code.md` — Beads' own take on the Claude Code integration;
  cross-check against our `beads-workflow` rule for drift
- `CHANGELOG.md` — version-specific behavior. The rule pins mechanics to bd versions
  (e.g. 1.0.2 worktree auto-detect); when bumping the assumed version, diff this first.

### CLI reference for the commands the rule actually issues

`site/cli-reference/` (107 files) is the authoritative per-command surface — **look up, do
not read through**. The commands our rule depends on:

- `create.md` (incl. `--type`, `--parent`, `--deps`) · `update.md` (`--status`) · `close.md`
- `comment.md · comments.md` — the AWAITING REVIEW / completion-comment protocol
- `show.md · list.md · ready.md` — dispatch loop (`bd ready` → work)
- `worktree.md` — `bd worktree create` (the rule bans raw `git worktree add`)
- `dep.md · epic.md · children.md` — epic/child structure and dependencies
- `prime.md · context.md` — session bootstrap (`bd prime`)
- `init.md · bootstrap.md · config.md` — setup + the `export.*` keys our bootstrap
  sets (`export.auto/git-add false` by default → Dolt-only sync; `--jsonl` flips them on)

---

## 🟡 Important — read soon

- `site/multi-agent/coordination.md · routing.md` — multiple agents over a shared DB; the
  rule's worktree-per-bead model is this in practice
- `guides/MULTI_REPO_MIGRATION.md` — multi-repo / OSS-contributor workflow (planning out of
  upstream PRs) — relevant if the rule grows multi-repo guidance
- `site/recovery/merge-conflicts.md · sync-failures.md · database-corruption.md` — failure
  modes the rule should anticipate (resilience-standard territory)
- `site/reference/git-integration.md` — how Beads interacts with git; informs the worktree
  and `.gitignore` rules
- `site/reference/configuration.md · faq.md · troubleshooting.md` — reference lookups
- `site/core-concepts/labels.md · metadata.md · hash-ids.md` — the rest of the data model

---

## 🟠 Situational — read when the case arises

- `site/workflows/{formulas,gates,molecules,wisps}.md` — higher-level workflow features; only
  if we adopt them in a rule
- `site/recovery/circular-dependencies.md · uninstalling.md`
- `site/reference/{advanced,antivirus}.md` — specialist topics
- `site/architecture/index.md` — orientation only; not needed to use `bd`
- `site/integrations/mcp-server.md` — only if we drive `bd` via MCP instead of the CLI
- the remaining ~95 files in `site/cli-reference/` — look up the exact command on demand

---

## 🔴 Skip — not relevant (and mostly not even vendored)

- **Beads repo-root agent files** (`CLAUDE.md`, `AGENTS.md`, `AGENT_INSTRUCTIONS.md`) — NOT
  vendored; they document contributing to Beads (Go, golangci-lint, make test), not using it
- **Other-agent integrations** (aider, cursor, codex, gemini, github-copilot, windsurf, …) —
  REMOVED by prune; we are the Claude ecosystem (kept: `claude-code`, `mcp-server`)
- **Upstream's raw `docs/` internals** — `staged-for-removal/`, `design/`, `adr/`, CI_*,
  TESTING*, INTERNALS, DOLT-BACKEND, COLLISION_MATH, etc. — never vendored
- `site/community-tools.md` — third-party tooling; ignore unless evaluating one
