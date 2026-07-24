# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

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
  `id`, `display_name`, `recipes`, `composes`, `template_root`, and
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
  `--body-file` and `--stdin` as valid alternatives to `-d/--description`
  for `bd create` (both flags exist in Beads v1.0.5+).

### Changed
- **Vendored Beads docs bumped to v1.1.0** (`8e4e59d3`, was `33e71d21`).
  161 files including the v1.1.0 docs on `--init-if-missing`, `bd metrics`
  consent flow, `bd prime` AGENTS.md/CLAUDE.md divergence reminder, sync
  repair cascade, and compaction-archive-before-discard.
- **`bootstrap.py` entry point moved to file end** — `if __name__ == "__main__"`
  was previously in the middle of the file, causing a `NameError` for
  `install_harness_adapters` when running as a script. Now at the bottom,
  with an inline comment explaining why it must stay there.

### Fixed
- **`install_harness_adapters` NameError on script-mode invocation** —
  `python bootstrap.py --harness omo` previously crashed with
  `NameError: name 'install_harness_adapters' is not defined` because the
  `if __name__ == "__main__":` block ran `main()` before the function
  definition was reached during top-level execution. Reordering fixes
  module-load and script-mode paths uniformly.

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

<!-- Reference template (delete on first release):
### Added — new features
### Changed — changes in existing functionality
### Deprecated — soon-to-be removed features
### Removed — now-removed features
### Fixed — bug fixes
### Security — vulnerability fixes
-->
