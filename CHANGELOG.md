# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

### Fixed
- **P0-1 — Swallowed TypeError in step 6 of `bootstrap_project`** — when
  `adapters.py` failed to import, the `except ImportError` fallback set
  `_resolve_harnesses = None`, and the bare call at the install-harness
  step surfaced as `TypeError: 'NoneType' object is not callable`. The
  `[BOOTSTRAP FAILED]` line printed only the exception type + message
  with no traceback, leaving the failure layer invisible. Fixes:
  - `bootstrap.py`: validate `_resolve_harnesses is not None` before
    calling; raise `RuntimeError("adapters module not importable; ...")`
    so the diagnostic names the failure layer.
  - `bootstrap.py`: print `traceback.print_exc()` to stderr on any
    bootstrap failure (was: only `f"{type(e).__name__}: {e}"`). Exit
    code 1 path is unchanged.
  - Tests: `tests/test_p0_1.py::TestAdaptersImportFailure` (4 tests
    covering RuntimeError surface, exit code, and traceback presence)
    and `TestBootstrapFailedExitCode` (1 regression guard).

## [3.9.0] - 2026-07-28

### Removed
- **Russian localization files dropped** — `templates/rules-ru/` (complete
  directory) and `templates/rules/communication-style.md` (Russian examples
  with English scaffolding). Also `.claude/rules/communication-style.md`
  and `.claude/rules/repository-scope.md` (both fully in Russian and
  referencing the `weselow/claude-protocol` upstream, which is not this
  fork's history). The English-only dev rules
  (`implementation-standard`, `logging`, `tdd`, `debugging`,
  `resilience`) remain and are the only `--with-rules` payload.
  The `--with-rules` help text is updated to list the actual files copied.

### Security
- **bash-guard `--force` bypass (F-01)** — the epic-close children audit
  used `command.includes('--force')` which a malicious bead description
  could exploit. Replaced with a position-aware token check: only `--force`
  in the flag section (before any description flag) is honoured; `--force`
  inside a description value is now treated as data, not as a flag.
  Adds 2 vitest regression tests.

### Added
- **Multi-harness adapters** — install Claude Code, OpenCode, OMP (oh-my-pi),
  OMO, and Codex CLI orchestrators in a single `bootstrap` run via
  `--harness {claude|codex|opencode|omp|omo|all}`. Composition is automatic:
  `omo` expands to `[omo, opencode, codex]`; `omp` installs the OpenCode
  extension plus the `.omp/` skill/rule layout. Adapter templates are pure
  CommonJS/JSON (no Node toolchain needed for the consumer) and ship both
  TypeScript source and bundled JavaScript for OpenCode 1.18+ plugin loaders
  and OMP 16.5+ extension loaders.
- **`install_harness_adapters` API** in `bootstrap.py` — resolves harness IDs
  to `HarnessAdapter` objects, expands compositions via `resolve()`, and
  materializes the per-harness tree (`.opencode/plugins/`, `.codex/config.toml`,
  `.omp/extensions/`, etc.) with idempotent writes. `bd setup opencode` /
  `bd setup codex` is invoked per adapter so Beads v1.1.0 native integration
  is wired in.
- **Adapter registry (`adapters.py`)** — `HarnessAdapter` dataclass with
  `id`, `display_name`, `recipes`, `composes`, `template_root`,
  `settings_in_install_root`, `project_instructions_at_root`, and
  `settings_filename`. Backed by `tests/test_adapters.py` (14 tests:
  composition, idempotency, recipes, marker placement).
- **`scripts/docs-sync-beads.sh`** + `mise run docs-sync-beads` task — refresh
  the vendored Beads documentation from the upstream `gastownhall/beads`
  repo at a pinned tag (default: `v1.1.0`). Prunes non-Claude integrations
  and records the upstream commit in `UPSTREAM_COMMIT.txt`.
- **Idempotent `bd init`** — bootstrap now uses `bd init --init-if-missing`
  when the installed Beads binary supports it (v1.1.0+). Probed once per
  process via `bd init --help` and cached. Re-running bootstrap on an
  already-initialized project no longer fails with "database already exists".
- **Beats `bash-guard` flag coverage** — `bash-guard.cjs` now accepts
  `--body-file`, `--stdin`, `--design`, `--design-file`, `--acceptance`,
  `--context`, `--notes`, and `--append-notes` as valid alternatives to
  `-d/--description` for `bd create`. `bd q` (Quick-Capture) is allowed
  without a description (auto-generates a placeholder).

### Changed
- **Vendored Beads docs bumped to v1.1.0** (`8e4e59d3`, was `33e71d21`).
  161 files including the v1.1.0 docs on `--init-if-missing`, `bd metrics`
  consent flow, `bd prime` AGENTS.md/CLAUDE.md divergence reminder, sync
  repair cascade, and compaction-archive-before-discard.
- **OpenCode runtime config path corrected** — `opencode.json` is now
  written to `./opencode.json` (project root) instead of
  `.opencode/opencode.json`. OpenCode 1.18+ reads only the root-level
  config; `.opencode/` is the capability root for plugins/agents/skills
  but not for runtime configuration. The plugin path inside `opencode.json`
  was updated to `./.opencode/plugins/claude-protocol.js` to match.
- **OpenCode / OMP agent-instructions file location** — `AGENTS.md` is now
  written to the project root (not `.opencode/AGENTS.md`) so OpenCode's
  working-directory discovery actually finds it. Same for OMP and OMO.
  Codex now uses `AGENTS.md` at the root as well (was wrongly `.codex/CLAUDE.md`).
- **Codex settings file** — was `.codex/settings.json` (no such schema in
  Codex CLI); now `.codex/config.toml` matching the real Codex layout.
- **bootstrap.py entry point moved to file end** — `if __name__ == "__main__"`
  was previously in the middle of the file, causing a `NameError` for
  `install_harness_adapters` when running as a script. Now at the bottom,
  with an inline comment explaining why it must stay there.
- **bash-guard `bd list` in epic-close check** — now passes `--all` so
  closed children are visible. Without it, a fully-closed epic would
  silently pass the audit. bash-guard.test.js fake-bd updated to mock
  both invocations.

### Fixed
- **`install_harness_adapters` NameError on script-mode invocation** —
  `python bootstrap.py --harness omo` previously crashed with
  `NameError: name 'install_harness_adapters' is not defined` because the
  `if __name__ == "__main__":` block ran `main()` before the function
  definition was reached during top-level execution. Reordering fixes
  module-load and script-mode paths uniformly.
- **Adapter path bug — leading dot dropped** — the previous
  `Path(adapter.install_root).relative_to(Path("."))` removed the leading
  dot from `.omp` / `.codex` etc. so files were written to `omp/` instead
  of `.omp/`. New `settings_destination()` / `agent_instructions_destination()`
  / `*_rel_key()` methods on `HarnessAdapter` model the path semantics
  explicitly and are used everywhere the installer writes.
- **Bootstrap hung on wedged Dolt server** — `subprocess.run(..., timeout=15)`
  in `install_beads()` raised `TimeoutExpired` but left a zombie process
  holding the `.beads` Dolt lock, which could wedge subsequent runs.
  Replaced with a `_run_bd_with_timeout()` helper that Popen's `bd`,
  uses `communicate(timeout=)` + `proc.kill()` on timeout. The capability
  probe for `--init-if-missing` uses the same helper.
- **OpenCode / OMP hooks fired twice** — both loaders scan `.ts` AND
  `.js` in the same directory; emitting both caused the same hook to
  fire twice (one module per loader). Bootstrap now installs only the
  bundled `.js`; the `.ts` source remains in `templates/` for
  maintainers.
- **OpenCode `permission.ask` empty command was treated as bash to
  validate** — the payload field is best-effort. Now treats missing
  `metadata.command` as "let through" and relies on `tool.execute.before`
  for primary enforcement.
- **Beads worktree-status label confusion** — clarified in
  `beads-workflow.md` that `local` IS the correct label for a worktree
  on the shared DB and is not a breakage. (`none` would mean a worktree
  with no beads at all.)
- **`bd restore` semantics** — workflow-Doc now notes it's read-only by
  default and requires `--apply` to actually write the original back.
- **`bd --type` enum** — workflow-Doc previously listed `spike` / `story`
  / `milestone` as built-in types; corrected to the actual built-in set
  (`bug | feature | task | epic | chore | decision`) plus the
  `enhancement`/`feat`/`dec`/`adr` aliases.
- **`bd dep relate` was recommended for follow-up** — it's a bidirectional
  soft-link ("see also"), not a follow-up marker. Replaced with
  `bd dep add --type supersedes` (or `--type discovered-from` /
  `--type relates-to` depending on intent).
- **AGENTS.md over-claimed universal rules auto-loading** — Claude Code
  reads `.claude/rules/`, OpenCode reads `AGENTS.md` + `opencode.json`
  `instructions`, OMP reads `.omp/RULES.md` and `alwaysApply`-flagged
  rules. Other harnesses may not auto-load any rules directory. Doc
  reflects the per-harness reality.

### Security
- **bash-guard `--no-verify` substring bypass (F-02)** — the original check
  used `command.includes('--no-verify')`, so a commit message whose text
  contained the string `--no-verify` was falsely blocked (UX), and a quoted
  string like `-m "use --no-verify carefully"` would have bypassed the
  intended block (F-02 family). Replaced with a shell-style tokenizer
  (`_shellTokens()` in `bash-guard.cjs`) that respects single- and
  double-quoted strings, and now only flags `--no-verify` when it appears
  as an unquoted argument. Adds 2 vitest regression tests (one for
  quoted-commit-message false positive, one for branch-name false positive).
- **Codex `config.toml` clobber (F-03)** — the Codex adapter shipped a
  4-line comment placeholder for `config.toml`, but the previous installer
  called `recorder.put_file` unconditionally, which would overwrite an
  existing user config (model, sandbox, approval_policy). `_write_settings_for_adapter`
  now detects a non-placeholder user config and records `"preserved"` in the
  change manifest instead of overwriting. Adds 2 pytest regression tests
  (preservation + fresh-install placeholder).
- **Hook template supply-chain (F-04)** — the six shipped `.cjs` hook
  templates are executed on every Bash/Edit/SessionStart event, but were
  installed without any integrity check. A tampered template would get
  RCE on every hook call. `copy_hooks()` now verifies each shipped hook
  against `_EXPECTED_HOOK_HASHES` (SHA-256) and refuses to install any
  template whose hash differs. The user can opt in with
  `--allow-untouched-hooks` for legitimate local hook development. Adds
  3 pytest regression tests (happy path + tamper refusal + bypass).
- **Hook template hash table** — `_EXPECTED_HOOK_HASHES` is a single
  module-level constant in `bootstrap.py`. Bump in lockstep with any edit
  to `templates/hooks/*.cjs` (a one-liner in the bootstrap.py constants
  block). The test suite asserts hash-table parity on first run.

### Changed
- **Subprocess timeouts centralised** — magic numbers (`5`, `10`, `15`,
  `180`, `2`) sprinkled across `subprocess.run(timeout=...)` calls are now
  named constants `_BD_TIMEOUT_SHORT/DEFAULT/LONG`, `_GIT_TIMEOUT`, and
  `_BD_OUTPUT_GRACE`. Tests can patch them in one place.
- **Dead code removed** — `_install_legacy_claude_artifacts` was a no-op
  stub left over from a v3.x migration; removed.

## [3.8.2] - 2026-05-12

### Added
- SHA-256 manifest tracks user file modifications during `npx claude-protocol upgrade`
- `softprops/action-gh-release` bumped v1 → v2 for Node 20 runtime in CI

### Fixed
- `bootstrap.py` verifies `bd export` config by read-back; sets `git-add` before
  auto-export so first commits don't lose the JSONL snapshot
- Cwd-independent hook paths; side-effect-free `--dry-run`; safe `.claude/`
  backups before overwrite

[3.8.1]: #comparing-v3.8.0..v3.8.1
[3.8.2]: #comparing-v3.8.1..v3.8.2
[3.9.0]: https://github.com/gardenbaum/claude-protocol/compare/v3.8.2...v3.9.0
