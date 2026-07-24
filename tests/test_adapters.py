"""Tests for adapters.py and the install_harness_adapters orchestrator."""

import json
import sys
from pathlib import Path

import pytest

import adapters as adapters_mod
import bootstrap
from adapters import (
    CLAUDE,
    OMP,
    OPENCODE,
    HarnessAdapter,
    resolve,
    validate,
)


sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _color_off_by_default():
    bootstrap.configure_color("never")
    yield
    bootstrap.configure_color("never")


def test_registry_includes_all_targeted_harnesses():
    expected = {"claude", "codex", "opencode", "pi", "omp", "omo"}
    assert {a.id for a in adapters_mod.ALL_ADAPTERS} == expected
    assert {a.id for a in resolve("all")} == expected


def test_validate_rejects_unknown_id():
    with pytest.raises(ValueError):
        validate("nope")


def test_resolve_returns_single_adapter_for_bare_id():
    assert [a.id for a in resolve("opencode")] == ["opencode"]


def test_resolve_expands_omo_into_opencode_plus_codex():
    ids = [a.id for a in resolve("omo")]
    assert ids[0] == "omo"
    assert set(ids) >= {"omo", "opencode", "codex"}


def test_opencode_adapter_uses_opencode_json_and_agents_md():
    assert OPENCODE.install_root == ".opencode"
    assert OPENCODE.agent_instructions_filename == "AGENTS.md"
    assert OPENCODE.settings_filename == "opencode.json"


def test_omp_adapter_uses_compiled_yarn_settings_and_agents_md():
    assert OMP.install_root == ".omp"
    assert OMP.agent_instructions_filename == "AGENTS.md"
    assert OMP.settings_filename == "config.yml"
    assert OMP.uses_shared_rules is True


def test_omp_plugin_artifacts_exist_on_disk():
    omp = adapters_mod.TEMPLATES_DIR / "omp"
    assert (omp / "extensions" / "claude-protocol.js").is_file()
    assert (omp / "extensions" / "claude-protocol.ts").is_file()
    assert (omp / "shared" / "runtime-policy.js").is_file()
    assert (omp / "config.yml").is_file()


def test_opencode_plugin_artifacts_exist_on_disk():
    oc = adapters_mod.TEMPLATES_DIR / "opencode"
    assert (oc / "plugins" / "claude-protocol.js").is_file()
    assert (oc / "plugins" / "claude-protocol.ts").is_file()
    assert (oc / "shared" / "runtime-policy.js").is_file()
    assert (oc / "opencode.json").is_file()


# ---------------------------------------------------------------------------
# install_harness_adapters — orchestrator smoke tests
# ---------------------------------------------------------------------------


def _recorder(tmp_path):
    return bootstrap.ChangeRecorder(tmp_path, {"files": {}}, force=False, dry_run=False)


def _bd_setup_mock_factory(tmp_path):
    """Return a fake subprocess.run that emulates ``bd setup <recipe> -o <p>``.

    Each call writes a complete Beads-marker block to the output path, so
    orchestrator marker placement can be tested end-to-end.
    """
    def fake_run(cmd, **kwargs):
        if "-o" in cmd:
            i = cmd.index("-o") + 1
            p = Path(kwargs["cwd"]) / cmd[i]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "<!-- BEGIN BEADS INTEGRATION v:1.1.0 profile:full hash:abc -->\n"
                "## Beads Issue Tracking\nUse `bd` for all task tracking.\n"
                "<!-- END BEADS INTEGRATION -->\n"
            )
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    return fake_run


def test_install_opencode_writes_plugin_and_shared_assets(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(bootstrap.subprocess, "run", _bd_setup_mock_factory(tmp_path))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: "/usr/bin/bd" if n == "bd" else None)

    bootstrap.install_harness_adapters(recorder, ["opencode"], "Demo")

    assert (tmp_path / ".opencode" / "plugins" / "claude-protocol.js").is_file()
    assert (tmp_path / ".opencode" / "shared" / "runtime-policy.js").is_file()
    assert (tmp_path / ".opencode" / "opencode.json").is_file()
    assert (tmp_path / ".opencode" / "agents" / "code-reviewer.md").is_file()
    assert (tmp_path / ".opencode" / "skills" / "project-discovery" / "SKILL.md").is_file()
    assert (tmp_path / ".opencode" / "AGENTS.md").is_file()

    cfg = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())
    assert "claude-protocol.js" in cfg["plugin"][0]
    assert cfg["$schema"] == "https://opencode.ai/config.json"

    agents_md = (tmp_path / ".opencode" / "AGENTS.md").read_text()
    # Beads block (from mock) AND orchestrator marker must both be present
    assert "BEGIN BEADS INTEGRATION" in agents_md
    assert agents_md.count("BEGIN CLAUDE-PROTOCOL ORCHESTRATION") == 1


def test_install_omp_writes_extension_config_rules_agents_and_beads_profile(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path)
    calls = []
    base = _bd_setup_mock_factory(tmp_path)

    def track(cmd, **kwargs):
        calls.append(cmd)
        return base(cmd, **kwargs)

    monkeypatch.setattr(bootstrap.subprocess, "run", track)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: "/usr/bin/bd" if n == "bd" else None)

    bootstrap.install_harness_adapters(recorder, ["omp"], "Demo")

    assert (tmp_path / ".omp" / "extensions" / "claude-protocol.js").is_file()
    assert (tmp_path / ".omp" / "shared" / "runtime-policy.js").is_file()
    assert (tmp_path / ".omp" / "config.yml").is_file()
    assert (tmp_path / ".omp" / "rules" / "beads-workflow.md").is_file()
    assert (tmp_path / ".omp" / "agents" / "merge-supervisor.md").is_file()
    assert (tmp_path / ".omp" / "skills" / "project-discovery" / "SKILL.md").is_file()
    assert (tmp_path / ".omp" / "AGENTS.md").is_file()
    # omp runs `bd setup opencode` with its own install_root
    omp_opencode_calls = [c for c in calls if c[:3] == ["bd", "setup", "opencode"]]
    assert omp_opencode_calls
    assert any(c[-1].endswith(".omp/AGENTS.md") for c in omp_opencode_calls)
    agents_md = (tmp_path / ".omp" / "AGENTS.md").read_text()
    assert "BEGIN BEADS INTEGRATION" in agents_md
    assert agents_md.count("BEGIN CLAUDE-PROTOCOL ORCHESTRATION") == 1
    assert "claude-protocol.js" in (tmp_path / ".omp" / "config.yml").read_text()


def test_install_claude_is_a_noop_in_install_harness_adapters(tmp_path):
    """The legacy claude adapter is a no-op here — bootstrap_project runs
    the v3.x copy_agents / copy_hooks / etc. flow itself, and
    install_harness_adapters would create duplicate ChangeRecorder rows."""
    recorder = _recorder(tmp_path)
    bootstrap.install_harness_adapters(recorder, ["claude"], "Demo")
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".opencode").exists()
    assert not (tmp_path / ".omp").exists()


def test_install_omo_composes_opencode_and_codex(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path)
    calls = []
    base = _bd_setup_mock_factory(tmp_path)

    def track(cmd, **kwargs):
        calls.append(cmd)
        return base(cmd, **kwargs)

    monkeypatch.setattr(bootstrap.subprocess, "run", track)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: "/usr/bin/bd" if n == "bd" else None)

    bootstrap.install_harness_adapters(recorder, ["omo"], "Demo")

    # Omo composes opencode — the opencode plugin lives under .opencode/
    assert (tmp_path / ".opencode" / "plugins" / "claude-protocol.js").is_file()
    # Omo composes codex — codex artifacts live under .codex/
    assert (tmp_path / ".codex" / "settings.json").is_file()
    # Omo-level marker file (.omo/AGENTS.md) present and contains the
    # Beads INTEGRATION block from the opencode recipe.
    assert (tmp_path / ".omo" / "AGENTS.md").is_file()
    assert "BEGIN BEADS INTEGRATION" in (tmp_path / ".omo" / "AGENTS.md").read_text()
    # bd setup opencode runs at least once during omo expansion
    assert any(c[:3] == ["bd", "setup", "opencode"] for c in calls)


def test_install_is_idempotent_does_not_duplicate_orchestrator_marker(tmp_path, monkeypatch):
    """A re-run must not stack multiple orchestrator markers, even though
    ``bd setup opencode`` rewrites the file from scratch each call."""
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(bootstrap.subprocess, "run", _bd_setup_mock_factory(tmp_path))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: "/usr/bin/bd" if n == "bd" else None)

    bootstrap.install_harness_adapters(recorder, ["opencode"], "Demo")
    first = (tmp_path / ".opencode" / "AGENTS.md").read_text()
    bootstrap.install_harness_adapters(recorder, ["opencode"], "Demo")
    second = (tmp_path / ".opencode" / "AGENTS.md").read_text()

    assert first.count("BEGIN CLAUDE-PROTOCOL ORCHESTRATION") == 1
    assert second.count("BEGIN CLAUDE-PROTOCOL ORCHESTRATION") == 1
    # Stable: the second invocation is a no-op for marker placement because
    # _write_agent_instructions_for_adapter short-circuits when the marker
    # is already present.
    assert first == second


def test_install_unknown_harness_raises_value_error(tmp_path):
    recorder = _recorder(tmp_path)
    with pytest.raises(ValueError, match="unknown harness"):
        bootstrap.install_harness_adapters(recorder, ["unknown"], "Demo")
