"""Reproducible fixture builder for bootstrap.py integration tests.

Each fixture is a self-contained git repository under a per-fixture directory.
The fixtures model the three distinct starting states where P0-1, P1-1..P1-6,
P2-1..P2-5, P3-1..P3-3 defects appear:

  (A) fresh clone — no .beads/ database; a bare remote that carries refs/dolt/data
      so the "no beads database found" failure path can be exercised.
  (B) second run — init has already executed once, so core.hooksPath=.beads-hooks
      is already set in this repo's git config.
  (C) genuine third-party hooks — core.hooksPath points at .beads-hooks/foreign/
      AND a .husky/ directory exists, so the "real conflict" path can fire.

Run as:

    python tests/fixtures/build_fixtures.py            # build all three
    python tests/fixtures/build_fixtures.py A          # build only A
    python tests/fixtures/build_fixtures.py A B C       # build specific

By default fixtures live in <repo>/tests/fixtures/_built/{A,B,C}. Pass --out to
override. Tests should reuse fixtures in place — re-running this script will
delete and rebuild the requested fixture(s).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures" / "_built"


def run(cmd: list[str], cwd: Path, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess; capture output. Print stderr on failure for debug."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        print(f"FAIL: {' '.join(cmd)} in {cwd}", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def init_git_repo(path: Path, user: str = "Fixture Bot",
                  email: str = "fixture@local") -> None:
    """Create a fresh git repo with one initial commit. Re-init is destructive."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    run(["git", "init", "--initial-branch=main"], cwd=path)
    run(["git", "config", "user.name", user], cwd=path)
    run(["git", "config", "user.email", email], cwd=path)
    run(["git", "config", "commit.gpgsign", "false"], cwd=path)
    (path / "README.md").write_text(f"# {path.name}\n\nfixture for claude-protocol\n")
    (path / ".gitignore").write_text("node_modules/\n")
    run(["git", "add", "-A"], cwd=path)
    run(["git", "commit", "-m", "initial"], cwd=path)


def build_remote_bare_with_dolt(remote_path: Path) -> None:
    """Build a bare remote that already carries refs/dolt/data + a main branch.

    Models a real upstream where beads history is already on the remote but the
    clone has not yet pulled it. This is the state that surfaces the
    'no beads database found' path during a fresh init.

    Implementation note: `git update-ref` silently refuses to create a ref
    pointing at an object that doesn't exist in the repo, so we hash-object a
    placeholder blob first and then point refs/dolt/data at it. The blob
    content is irrelevant — only the ref's existence matters for bootstrap.
    """
    if remote_path.exists():
        shutil.rmtree(remote_path)
    remote_path.mkdir(parents=True)
    run(["git", "init", "--bare", "--initial-branch=main"], cwd=remote_path)
    run(["git", "-C", str(remote_path), "symbolic-ref", "HEAD",
         "refs/heads/main"], cwd=remote_path)
    placeholder_blob = subprocess.run(
        ["git", "-C", str(remote_path), "hash-object", "-w", "--stdin"],
        input=b"placeholder-dolt-blob", capture_output=True, check=True,
    ).stdout.decode().strip()
    run(["git", "-C", str(remote_path), "update-ref",
         "refs/dolt/data", placeholder_blob], cwd=remote_path)


def attach_remote(repo: Path, remote_path: Path) -> None:
    run(["git", "remote", "add", "origin", str(remote_path)], cwd=repo)
    # Push the initial commit so origin/main exists too.
    run(["git", "push", "-u", "origin", "main"], cwd=repo)


def build_a(out: Path) -> Path:
    """Fixture A: fresh clone — no .beads/, has refs/dolt/data on origin."""
    path = out / "A"
    remote = out / "A-origin"
    build_remote_bare_with_dolt(remote)
    init_git_repo(path)
    attach_remote(path, remote)
    print(f"[A] built at {path} (remote with refs/dolt/data at {remote})")
    return path


def build_b(out: Path) -> Path:
    """Fixture B: prior init run — core.hooksPath=.beads-hooks already set.

    Created by initializing a fresh repo, then running bootstrap once. The
    bootstrap command itself is not run here; we model the post-init state
    directly so this fixture is fast and deterministic. The state modelled:

      - core.hooksPath = .beads-hooks
      - .beads-hooks/ directory present with at least one shim file
      - .beads/ NOT present (it's gitignored under fixture A's setup) so init
        code-path that creates .beads/ can be exercised
    """
    path = out / "B"
    if path.exists():
        shutil.rmtree(path)
    init_git_repo(path)
    # Simulate a previous successful init that left only the hooks wiring.
    hooks_dir = path / ".beads-hooks"
    hooks_dir.mkdir()
    (hooks_dir / "post-merge").write_text("#!/bin/sh\necho bd dolt pull\n")
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\necho bd export\n")
    run(["git", "config", "core.hooksPath", ".beads-hooks"], cwd=path)
    run(["git", "add", "-A"], cwd=path)
    run(["git", "commit", "-m", "simulate prior init"], cwd=path)
    print(f"[B] built at {path} (core.hooksPath=.beads-hooks, "
          f".beads-hooks/ pre-populated)")
    return path


def build_c(out: Path) -> Path:
    """Fixture C: genuine third-party hooks.

    core.hooksPath points at .beads-hooks/foreign/ AND a .husky/ directory
    exists. This is the state where the 'real conflict' path should fire —
    the second-run code should refuse to overwrite these and tell the user
    explicitly.
    """
    path = out / "C"
    if path.exists():
        shutil.rmtree(path)
    init_git_repo(path)
    foreign = path / ".beads-hooks" / "foreign"
    foreign.mkdir(parents=True)
    (foreign / "pre-commit").write_text("#!/bin/sh\necho foreign pre-commit\n")
    husky = path / ".husky"
    husky.mkdir()
    (husky / "pre-commit").write_text("npx lint-staged\n")
    run(["git", "config", "core.hooksPath", str(foreign.relative_to(path))],
        cwd=path)
    run(["git", "add", "-A"], cwd=path)
    run(["git", "commit", "-m", "third-party hooks present"], cwd=path)
    print(f"[C] built at {path} (core.hooksPath=.beads-hooks/foreign + .husky/)")
    return path


BUILDERS = {"A": build_a, "B": build_b, "C": build_c}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", nargs="*", default=["A", "B", "C"],
                        help="Which fixtures to build (default: all).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Output root directory.")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in args.which:
        if name not in BUILDERS:
            print(f"unknown fixture: {name}", file=sys.stderr)
            return 2
        BUILDERS[name](args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
