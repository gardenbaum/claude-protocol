"""Tests for P0-1: swallowed TypeError in step 6.

Three defects to verify:

(a) The traceback is swallowed. [BOOTSTRAP FAILED] prints only
    type + msg, no stack frame. A user should never have to clone the
    repo to find out where it broke. Fix: print traceback to stderr on
    failure (or raise on --debug).

(b) The actual bug. bootstrap.py calls install_harness_adapters with
    _resolve_harnesses(harness). When adapters.py fails to import, the
    except ImportError branch sets _resolve_harnesses = None, and
    None(harness) is the 'NoneType object is not callable' TypeError.
    Fix: validate _resolve_harnesses is not None BEFORE calling it;
    raise RuntimeError with the chained ImportError so the diagnostic
    is 'adapters module not importable' instead of an opaque NoneType.

(c) Exit code. Verify init exits non-zero on failure. The existing
    code returns 1 in the except branch, but assert this as a
    regression guard.
"""

from __future__ import annotations

import io
import logging
import subprocess
import sys
from pathlib import Path

import pytest

# Add project root to path so we can import bootstrap
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bootstrap


# ---------------------------------------------------------------------------
# Helpers: stub the heavy install steps so bootstrap_project is offline +
# fast. The point of these tests is the harness-resolution path, not the
# actual install steps.
# ---------------------------------------------------------------------------


def _stub_heavy_steps(monkeypatch: pytest.MonkeyPatch,
                      tmp_path: Path) -> None:
    """Replace the side-effecting install steps with no-ops.

    install_beads is replaced with a True-returning lambda so bootstrap.py
    proceeds to the try block. The remaining copy_* / setup_* functions are
    stubbed so the test does not touch templates/, .claude/, etc.
    """
    monkeypatch.setattr(bootstrap, "install_beads",
                        lambda *a, **kw: True)
    monkeypatch.setattr(bootstrap, "copy_agents", lambda *a, **kw: [])
    monkeypatch.setattr(bootstrap, "copy_hooks", lambda *a, **kw: None)
    monkeypatch.setattr(bootstrap, "copy_rules_and_skills",
                        lambda *a, **kw: [])
    monkeypatch.setattr(bootstrap, "copy_settings_and_claude_md",
                        lambda *a, **kw: None)
    monkeypatch.setattr(bootstrap, "setup_gitignore",
                        lambda *a, **kw: None)
    # pre-seed an empty manifest so save_manifest is happy
    (tmp_path / ".claude").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# (b) The actual bug: _resolve_harnesses is None
# ---------------------------------------------------------------------------


class TestAdaptersImportFailure:
    """When adapters.py fails to import, bootstrap must raise a clear error.

    The except ImportError branch sets _resolve_harnesses = None.
    Calling None(harness) is the original TypeError. The fix is to
    validate the symbol is callable BEFORE calling it and raise
    RuntimeError with a message that names the failure (so the user
    knows the fix is at the import layer, not somewhere deep in
    install_harness_adapters).
    """

    def test_resolve_harnesses_none_raises_runtimeerror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """_resolve_harnesses = None must surface as RuntimeError, not TypeError."""
        monkeypatch.setattr(bootstrap, "_resolve_harnesses", None)
        _stub_heavy_steps(monkeypatch, tmp_path)

        with pytest.raises(RuntimeError) as exc_info:
            bootstrap.bootstrap_project(
                project_dir=tmp_path, project_name="t",
                with_rules=False, force=False, upgrade=False,
                dry_run=False, harness="claude",
            )

        msg = str(exc_info.value)
        # Diagnostic must name the failure layer; reject silent TypeError.
        assert "adapters" in msg.lower(), (
            f"RuntimeError message must mention 'adapters'; got: {msg!r}"
        )

    def test_resolve_harnesses_none_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
    ) -> None:
        """bootstrap_project must return 1 (not 0, not 2) when steps fail."""
        monkeypatch.setattr(bootstrap, "_resolve_harnesses", None)
        _stub_heavy_steps(monkeypatch, tmp_path)

        rc = bootstrap.bootstrap_project(
            project_dir=tmp_path, project_name="t",
            with_rules=False, force=False, upgrade=False,
            dry_run=False, harness="claude",
        )
        # bootstrap_project must NOT return 0 on any failure path.
        assert rc != 0, "bootstrap_project returned 0 despite RuntimeError"

    def test_resolve_harnesses_none_prints_traceback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
    ) -> None:
        """The user-visible BOOTSTRAP FAILED message must include a frame.

        The bug: '[BOOTSTRAP FAILED] TypeError: NoneType object is not callable'
        with no traceback. After the fix, the printed message must include
        either a frame (e.g. 'File "...bootstrap.py", line N') or a chained
        diagnostic naming the failure layer.
        """
        monkeypatch.setattr(bootstrap, "_resolve_harnesses", None)
        _stub_heavy_steps(monkeypatch, tmp_path)

        # bootstrap_project prints to stdout; capture both streams so the
        # test is robust against future stderr-only debug output.
        bootstrap.bootstrap_project(
            project_dir=tmp_path, project_name="t",
            with_rules=False, force=False, upgrade=False,
            dry_run=False, harness="claude",
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err

        assert "BOOTSTRAP FAILED" in combined
        # Must NOT be just '<ExceptionType>: <msg>' with no frame.
        # The fix prints traceback.format_exc() which includes 'File "..."'.
        # If the fix chose a different strategy (e.g. chained raise with
        # __cause__), accept that as long as the message names the cause.
        has_frame = "File \"" in combined
        has_cause = "adapters" in combined.lower()
        assert has_frame or has_cause, (
            "BOOTSTRAP FAILED must include a stack frame OR name 'adapters' "
            f"as the failure cause. Got: {combined!r}"
        )


# ---------------------------------------------------------------------------
# (c) Exit code regression guard
# ---------------------------------------------------------------------------


class TestBootstrapFailedExitCode:
    """Any failure inside the try block must return a non-zero exit code."""

    def test_step_exception_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """If any step raises, bootstrap_project returns 1 (not 0, not 2)."""

        def boom(*a, **kw):
            raise RuntimeError("simulated step failure")

        _stub_heavy_steps(monkeypatch, tmp_path)
        # Replace one of the steps with a raiser. The try/except at the
        # call site must catch and surface a non-zero exit code.
        monkeypatch.setattr(bootstrap, "copy_hooks", boom)

        rc = bootstrap.bootstrap_project(
            project_dir=tmp_path, project_name="t",
            with_rules=False, force=False, upgrade=False,
            dry_run=False, harness="claude",
        )
        assert rc == 1, f"expected exit code 1, got {rc}"
