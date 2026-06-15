"""Tests for bootstrap.py — project name inference, copy_and_replace, setup_gitignore, manifest."""

import json
import sys
from pathlib import Path

import pytest

# Add project root to path so we can import bootstrap
sys.path.insert(0, str(Path(__file__).parent.parent))

import bootstrap
from bootstrap import (
    infer_project_name,
    copy_and_replace,
    setup_gitignore,
    configure_beads_sync,
    install_beads,
    _from_package_json,
    _from_pyproject,
    _from_cargo,
    _from_go_mod,
    file_sha256,
    content_sha256,
    load_manifest,
    save_manifest,
    should_update_file,
    save_upgrade,
    cleanup_obsolete,
    run_bd_doctor,
    _auto_inject_legacy_files,
    _memory_dir_should_skip,
    _cleanup_empty_local_settings,
    TEMPLATES_DIR,
)


# ============================================================================
# infer_project_name
# ============================================================================

class TestInferProjectName:
    def test_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "my-cool-app"}))
        assert infer_project_name(tmp_path) == "My Cool App"

    def test_from_package_json_with_scope(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "@org/my-package"}))
        assert infer_project_name(tmp_path) == "@Org/My Package"

    def test_from_package_json_underscores(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "my_cool_app"}))
        assert infer_project_name(tmp_path) == "My Cool App"

    def test_from_package_json_empty_name(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": ""}))
        # Falls through to directory name
        result = infer_project_name(tmp_path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_from_package_json_malformed(self, tmp_path):
        (tmp_path / "package.json").write_text("not json {{{")
        # Falls through to directory name
        result = infer_project_name(tmp_path)
        assert isinstance(result, str)

    def test_from_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module github.com/user/my-project\n\ngo 1.21\n")
        assert infer_project_name(tmp_path) == "My Project"

    def test_from_go_mod_simple_module(self, tmp_path):
        (tmp_path / "go.mod").write_text("module myapp\n")
        assert infer_project_name(tmp_path) == "Myapp"

    def test_fallback_to_directory_name(self, tmp_path):
        result = infer_project_name(tmp_path)
        # tmp_path has a generated name, but it should be titlecased
        assert isinstance(result, str)
        assert len(result) > 0

    def test_directory_name_dashes_to_spaces(self, tmp_path):
        project_dir = tmp_path / "my-awesome-project"
        project_dir.mkdir()
        assert infer_project_name(project_dir) == "My Awesome Project"

    def test_directory_name_underscores_to_spaces(self, tmp_path):
        project_dir = tmp_path / "my_awesome_project"
        project_dir.mkdir()
        assert infer_project_name(project_dir) == "My Awesome Project"

    def test_priority_package_json_over_go_mod(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "node-app"}))
        (tmp_path / "go.mod").write_text("module github.com/user/go-app\n")
        assert infer_project_name(tmp_path) == "Node App"


class TestFromPackageJson:
    def test_returns_none_when_missing(self, tmp_path):
        assert _from_package_json(tmp_path) is None

    def test_returns_none_for_empty_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert _from_package_json(tmp_path) is None


class TestFromPyproject:
    def test_returns_none_when_missing(self, tmp_path):
        assert _from_pyproject(tmp_path) is None

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib requires Python 3.11+")
    def test_reads_project_name(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-python-lib"\n'
        )
        assert _from_pyproject(tmp_path) == "My Python Lib"

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib requires Python 3.11+")
    def test_reads_poetry_name(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "poetry-project"\n'
        )
        assert _from_pyproject(tmp_path) == "Poetry Project"


class TestFromCargo:
    def test_returns_none_when_missing(self, tmp_path):
        assert _from_cargo(tmp_path) is None

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib requires Python 3.11+")
    def test_reads_package_name(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "rust-cli"\nversion = "0.1.0"\n'
        )
        assert _from_cargo(tmp_path) == "Rust Cli"


class TestFromGoMod:
    def test_returns_none_when_missing(self, tmp_path):
        assert _from_go_mod(tmp_path) is None

    def test_extracts_last_segment(self, tmp_path):
        (tmp_path / "go.mod").write_text("module github.com/org/my-service\n")
        assert _from_go_mod(tmp_path) == "My Service"


# ============================================================================
# copy_and_replace
# ============================================================================

class TestCopyAndReplace:
    def test_replaces_placeholder(self, tmp_path):
        source = tmp_path / "template.md"
        source.write_text("# [Project] Guide\n\nWelcome to [Project].")
        dest = tmp_path / "output" / "guide.md"

        copy_and_replace(source, dest, {"[Project]": "My App"})

        result = dest.read_text()
        assert result == "# My App Guide\n\nWelcome to My App."

    def test_creates_parent_dirs(self, tmp_path):
        source = tmp_path / "src.txt"
        source.write_text("content")
        dest = tmp_path / "a" / "b" / "c" / "file.txt"

        copy_and_replace(source, dest, {})

        assert dest.exists()
        assert dest.read_text() == "content"

    def test_multiple_replacements(self, tmp_path):
        source = tmp_path / "tmpl.txt"
        source.write_text("[Name] uses [Lang]")
        dest = tmp_path / "out.txt"

        copy_and_replace(source, dest, {"[Name]": "MyApp", "[Lang]": "Python"})

        assert dest.read_text() == "MyApp uses Python"

    def test_no_replacements(self, tmp_path):
        source = tmp_path / "tmpl.txt"
        source.write_text("unchanged content")
        dest = tmp_path / "out.txt"

        copy_and_replace(source, dest, {})

        assert dest.read_text() == "unchanged content"


# ============================================================================
# setup_gitignore
# ============================================================================

class TestSetupGitignore:
    def test_creates_gitignore_when_missing(self, tmp_path, capsys):
        setup_gitignore(tmp_path)

        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".worktrees/" in content

    def test_does_not_ignore_whole_beads_dir(self, tmp_path, capsys):
        """The tracker travels with the repo — .beads/ must NOT be ignored
        (that would hide the canonical .beads/issues.jsonl)."""
        setup_gitignore(tmp_path)

        lines = (tmp_path / ".gitignore").read_text().splitlines()
        assert ".beads/" not in lines
        assert ".beads" not in lines

    def test_does_not_ignore_issues_jsonl(self, tmp_path, capsys):
        """The readable .beads/issues.jsonl backup must stay git-tracked."""
        setup_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert "issues.jsonl" not in content

    def test_appends_missing_entries(self, tmp_path, capsys):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n.env\n")

        setup_gitignore(tmp_path)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".env" in content
        assert ".worktrees/" in content

    def test_skips_when_already_configured(self, tmp_path, capsys):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            "node_modules/\n.worktrees/\n.claude/.upgrades/\n"
        )

        setup_gitignore(tmp_path)

        content = gitignore.read_text()
        assert content.count(".worktrees/") == 1
        assert content.count(".claude/.upgrades/") == 1

    def test_adds_newline_if_missing(self, tmp_path, capsys):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/")  # no trailing newline

        setup_gitignore(tmp_path)

        content = gitignore.read_text()
        assert ".worktrees/" in content
        # Should have added a newline before the section
        assert "node_modules/\n" in content

    def test_idempotent_no_duplicates(self, tmp_path, capsys):
        """Running setup_gitignore twice must not duplicate any entry."""
        setup_gitignore(tmp_path)
        setup_gitignore(tmp_path)

        content = (tmp_path / ".gitignore").read_text()
        assert content.count(".worktrees/") == 1
        assert content.count(".claude/.upgrades/") == 1

    def test_detects_entries_without_trailing_slash(self, tmp_path, capsys):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".worktrees\n.claude/.upgrades\n")

        setup_gitignore(tmp_path)

        content = gitignore.read_text()
        # Should detect ".worktrees" matches ".worktrees/" and not add duplicate
        assert content.count(".worktrees") == 1

    def test_adds_upgrades_entry_on_first_run(self, tmp_path, capsys):
        """setup_gitignore writes .claude/.upgrades/ when it's missing."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")

        setup_gitignore(tmp_path)

        content = gitignore.read_text()
        assert ".claude/.upgrades/" in content

    def test_upgrades_entry_not_duplicated_on_rerun(self, tmp_path, capsys):
        """Running setup_gitignore twice must not duplicate .claude/.upgrades/."""
        setup_gitignore(tmp_path)
        setup_gitignore(tmp_path)

        content = (tmp_path / ".gitignore").read_text()
        assert content.count(".claude/.upgrades/") == 1

    def test_warns_when_issues_jsonl_is_gitignored(self, tmp_path, monkeypatch, capsys):
        """A pre-existing ignore of .beads/issues.jsonl breaks bd auto-export —
        setup_gitignore must surface that, not stay silent."""
        monkeypatch.setattr(bootstrap, "_path_is_gitignored",
                            lambda d, rel: rel == ".beads/issues.jsonl")
        setup_gitignore(tmp_path)
        out = capsys.readouterr().out
        assert ".beads/issues.jsonl" in out
        assert "gitignored" in out

    def test_no_conflict_warning_when_not_ignored(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(bootstrap, "_path_is_gitignored", lambda d, rel: False)
        setup_gitignore(tmp_path)
        assert "gitignored" not in capsys.readouterr().out


# ============================================================================
# install_beads — dry-run must not mutate
# ============================================================================

class TestInstallBeadsDryRun:
    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _record_runs(self, monkeypatch):
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._FakeResult()
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        return calls

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, capsys):
        """--dry-run must not run bd init / config / hooks (no side effects)."""
        calls = self._record_runs(monkeypatch)

        result = install_beads(tmp_path, dry_run=True)

        assert result is True
        assert calls == []                          # no bd subprocess at all
        assert not (tmp_path / ".beads").exists()   # nothing created on disk
        assert "dry-run" in capsys.readouterr().out.lower()

    def test_non_dry_run_still_configures(self, tmp_path, monkeypatch, capsys):
        """Without dry-run the sync config is still wired (regression guard)."""
        calls = self._record_runs(monkeypatch)
        monkeypatch.setattr(bootstrap, "_git_origin_url", lambda _: None)
        (tmp_path / ".beads").mkdir()  # skip the bd-init branch

        install_beads(tmp_path, dry_run=False)

        assert ["bd", "config", "set", "export.auto", "true"] in calls


# ============================================================================
# configure_beads_sync
# ============================================================================

class TestConfigureBeadsSync:
    def _patch(self, monkeypatch, origin="git@github.com:o/r.git"):
        calls = []
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        monkeypatch.setattr(bootstrap, "_git_origin_url", lambda _: origin)
        return calls

    def test_enables_export_and_wires_sync(self, tmp_path, monkeypatch, capsys):
        calls = self._patch(monkeypatch)
        result = configure_beads_sync(tmp_path)
        assert result is True
        assert ["bd", "config", "set", "export.auto", "true"] in calls
        assert ["bd", "config", "set", "export.git-add", "true"] in calls
        assert ["bd", "config", "set", "dolt.auto-push", "true"] in calls
        assert ["bd", "dolt", "remote", "add", "origin", "git@github.com:o/r.git"] in calls
        assert ["bd", "hooks", "install", "--shared"] in calls

    def test_skips_dolt_remote_without_origin(self, tmp_path, monkeypatch, capsys):
        calls = self._patch(monkeypatch, origin=None)
        configure_beads_sync(tmp_path)
        assert not any(c[:3] == ["bd", "dolt", "remote"] for c in calls)
        # still installs shared hooks for local-only repos
        assert ["bd", "hooks", "install", "--shared"] in calls

    def test_returns_false_when_bd_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)
        assert configure_beads_sync(tmp_path) is False

    def test_does_not_raise_on_timeout(self, tmp_path, monkeypatch, capsys):
        def fake_run(*a, **k):
            raise bootstrap.subprocess.TimeoutExpired(cmd="bd", timeout=15)
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        monkeypatch.setattr(bootstrap, "_git_origin_url", lambda _: None)
        # must not raise
        configure_beads_sync(tmp_path)


# ============================================================================
# _install_shared_hooks — must not hijack existing git hooks
# ============================================================================

class TestInstallSharedHooks:
    def _patch(self, monkeypatch, hooks_path=None):
        calls = []
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        monkeypatch.setattr(bootstrap, "_git_config_get", lambda _d, _k: hooks_path)
        return calls

    def test_installs_when_no_existing_hooks(self, tmp_path, monkeypatch, capsys):
        calls = self._patch(monkeypatch, hooks_path=None)
        bootstrap._install_shared_hooks(tmp_path)
        assert ["bd", "hooks", "install", "--shared"] in calls

    def test_installs_when_hookspath_is_beads(self, tmp_path, monkeypatch, capsys):
        """Re-running with bd's own hooksPath already set is fine."""
        calls = self._patch(monkeypatch, hooks_path=".beads-hooks")
        bootstrap._install_shared_hooks(tmp_path)
        assert ["bd", "hooks", "install", "--shared"] in calls

    def test_skips_when_existing_hookspath(self, tmp_path, monkeypatch, capsys):
        calls = self._patch(monkeypatch, hooks_path=".husky")
        bootstrap._install_shared_hooks(tmp_path)
        assert ["bd", "hooks", "install", "--shared"] not in calls
        assert "WARNING" in capsys.readouterr().out

    def test_skips_when_husky_dir_present(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".husky").mkdir()
        calls = self._patch(monkeypatch, hooks_path=None)
        bootstrap._install_shared_hooks(tmp_path)
        assert ["bd", "hooks", "install", "--shared"] not in calls
        assert "WARNING" in capsys.readouterr().out


# ============================================================================
# _run_bd / _git_origin_url helpers
# ============================================================================

class TestRunBd:
    def test_returns_true_on_success(self, tmp_path, monkeypatch, capsys):
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            return FakeResult()
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

        assert bootstrap._run_bd(["config", "set", "x", "y"], tmp_path, "set x") is True
        assert calls == [(["bd", "config", "set", "x", "y"], tmp_path)]

    def test_returns_false_when_bd_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)
        assert bootstrap._run_bd(["x"], tmp_path, "x") is False

    def test_returns_false_on_timeout(self, tmp_path, monkeypatch, capsys):
        def fake_run(*a, **k):
            raise bootstrap.subprocess.TimeoutExpired(cmd="bd", timeout=15)
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        assert bootstrap._run_bd(["x"], tmp_path, "x") is False

    def test_returns_false_on_nonzero_exit(self, tmp_path, monkeypatch, capsys):
        class FakeResult:
            returncode = 1
            stdout = ""
            stderr = "boom"
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: FakeResult())
        assert bootstrap._run_bd(["x"], tmp_path, "x") is False

    def test_returns_false_on_oserror(self, tmp_path, monkeypatch, capsys):
        def fake_run(*a, **k):
            raise OSError("cannot start bd")
        monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/bd")
        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        assert bootstrap._run_bd(["x"], tmp_path, "x") is False


class TestGitOriginUrl:
    def test_returns_url_when_origin_set(self, tmp_path, monkeypatch):
        class FakeResult:
            returncode = 0
            stdout = "git@github.com:o/r.git\n"
            stderr = ""
        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: FakeResult())
        assert bootstrap._git_origin_url(tmp_path) == "git@github.com:o/r.git"

    def test_returns_none_when_no_origin(self, tmp_path, monkeypatch):
        class FakeResult:
            returncode = 128
            stdout = ""
            stderr = "no such remote"
        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: FakeResult())
        assert bootstrap._git_origin_url(tmp_path) is None


# ============================================================================
# Templates directory
# ============================================================================

class TestTemplatesDir:
    def test_templates_dir_exists(self):
        assert TEMPLATES_DIR.exists(), f"Templates dir not found: {TEMPLATES_DIR}"

    def test_has_hooks(self):
        hooks_dir = TEMPLATES_DIR / "hooks"
        assert hooks_dir.exists()
        hooks = list(hooks_dir.glob("*.cjs"))
        assert len(hooks) >= 6  # At least 6 hook files

    def test_has_agents(self):
        agents_dir = TEMPLATES_DIR / "agents"
        assert agents_dir.exists()
        agents = list(agents_dir.glob("*.md"))
        assert len(agents) >= 2  # code-reviewer + merge-supervisor

    def test_has_settings_json(self):
        assert (TEMPLATES_DIR / "settings.json").exists()

    def test_has_claude_md(self):
        assert (TEMPLATES_DIR / "CLAUDE.md").exists()

    def test_has_beads_workflow_rule(self):
        assert (TEMPLATES_DIR / "rules" / "beads-workflow.md").exists()


# ============================================================================
# Manifest functions
# ============================================================================

class TestFileSha256:
    def test_returns_sha256_prefixed_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = file_sha256(f)
        assert result.startswith("sha256:")
        assert len(result) == 7 + 64  # "sha256:" + 64 hex chars

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        assert file_sha256(f1) == file_sha256(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert file_sha256(f1) != file_sha256(f2)


class TestContentSha256:
    def test_matches_file_sha256(self, tmp_path):
        text = "hello world"
        f = tmp_path / "test.txt"
        f.write_text(text, encoding="utf-8")
        assert content_sha256(text) == file_sha256(f)


class TestLoadManifest:
    def test_returns_empty_when_no_manifest(self, tmp_path):
        m = load_manifest(tmp_path)
        assert m["version"] is None
        assert m["files"] == {}

    def test_reads_existing_manifest(self, tmp_path):
        manifest_dir = tmp_path / ".claude"
        manifest_dir.mkdir()
        data = {"version": "3.1.0", "installed_at": "2026-01-01", "files": {"a": "sha256:abc"}}
        (manifest_dir / ".manifest.json").write_text(json.dumps(data))
        m = load_manifest(tmp_path)
        assert m["version"] == "3.1.0"
        assert m["files"]["a"] == "sha256:abc"

    def test_returns_empty_on_corrupt_json(self, tmp_path):
        manifest_dir = tmp_path / ".claude"
        manifest_dir.mkdir()
        (manifest_dir / ".manifest.json").write_text("not json {{{")
        m = load_manifest(tmp_path)
        assert m["files"] == {}


class TestSaveManifest:
    def test_creates_manifest_file(self, tmp_path):
        data = {"version": "3.2.0", "installed_at": "now", "files": {"x": "sha256:123"}}
        save_manifest(tmp_path, data)
        path = tmp_path / ".claude" / ".manifest.json"
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["version"] == "3.2.0"
        assert loaded["files"]["x"] == "sha256:123"

    def test_overwrites_existing_manifest(self, tmp_path):
        save_manifest(tmp_path, {"version": "1", "installed_at": "", "files": {}})
        save_manifest(tmp_path, {"version": "2", "installed_at": "", "files": {"a": "b"}})
        loaded = json.loads((tmp_path / ".claude" / ".manifest.json").read_text())
        assert loaded["version"] == "2"


class TestShouldUpdateFile:
    def test_new_file(self, tmp_path):
        f = tmp_path / "new.md"
        ok, reason = should_update_file(f, "rules/new.md", {"files": {}}, False)
        assert ok is True
        assert reason == "new"

    def test_unchanged_file(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("original content", encoding="utf-8")
        h = file_sha256(f)
        manifest = {"files": {"rules/rule.md": h}}
        ok, reason = should_update_file(f, "rules/rule.md", manifest, False)
        assert ok is True
        assert reason == "unchanged"

    def test_modified_file(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("original content", encoding="utf-8")
        h = file_sha256(f)
        manifest = {"files": {"rules/rule.md": h}}
        # User modifies the file
        f.write_text("user modified content", encoding="utf-8")
        ok, reason = should_update_file(f, "rules/rule.md", manifest, False)
        assert ok is False
        assert reason == "modified"

    def test_force_overrides_modified(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("user modified", encoding="utf-8")
        manifest = {"files": {"rules/rule.md": "sha256:old"}}
        ok, reason = should_update_file(f, "rules/rule.md", manifest, True)
        assert ok is True
        assert reason == "forced"

    def test_legacy_install_no_manifest_entry(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("some content", encoding="utf-8")
        manifest = {"files": {}}
        ok, reason = should_update_file(f, "rules/rule.md", manifest, False)
        assert ok is False
        assert reason == "no_manifest"

    def test_force_overrides_legacy(self, tmp_path):
        f = tmp_path / "rule.md"
        f.write_text("some content", encoding="utf-8")
        manifest = {"files": {}}
        ok, reason = should_update_file(f, "rules/rule.md", manifest, True)
        assert ok is True
        assert reason == "forced"


class TestSaveUpgrade:
    def test_saves_to_upgrades_dir(self, tmp_path):
        save_upgrade(tmp_path, "rules/beads-workflow.md", "new content")
        dest = tmp_path / ".claude" / ".upgrades" / "rules" / "beads-workflow.md"
        assert dest.exists()
        assert dest.read_text() == "new content"

    def test_creates_nested_dirs(self, tmp_path):
        save_upgrade(tmp_path, "agents/code-reviewer.md", "v2 content")
        dest = tmp_path / ".claude" / ".upgrades" / "agents" / "code-reviewer.md"
        assert dest.exists()


# ============================================================================
# cleanup_obsolete
# ============================================================================

class TestCleanupObsolete:
    def test_empty_lists_noop(self, tmp_path, monkeypatch):
        """Empty OBSOLETE_* lists → empty report, no backup dir, no changes."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        (tmp_path / "foo.txt").write_text("hello")
        manifest = {"files": {"foo.txt": "sha256:abc"}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert report["removed_files"] == []
        assert report["removed_dirs"] == []
        assert report["stripped_settings_hooks"] == []
        assert report["stripped_local_patterns"] == []
        assert report["backups"][0] is None
        assert not (tmp_path / ".claude" / ".upgrades").exists()
        # File untouched, manifest untouched
        assert (tmp_path / "foo.txt").exists()
        assert manifest["files"] == {"foo.txt": "sha256:abc"}

    def test_removes_manifest_file(self, tmp_path, monkeypatch):
        """File in OBSOLETE_FILES + manifest → removed and backed up."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", ["foo.txt"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        target = tmp_path / "foo.txt"
        target.write_text("obsolete content")
        manifest = {"files": {"foo.txt": "sha256:abc"}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert "foo.txt" in report["removed_files"]
        assert not target.exists()
        assert "foo.txt" not in manifest["files"]
        # Backup exists
        backup_root = Path(report["backups"][0])
        assert backup_root.exists()
        backup_file = backup_root / "obsolete" / "foo.txt"
        assert backup_file.exists()
        assert backup_file.read_text() == "obsolete content"

    def test_skips_non_manifest_file(self, tmp_path, monkeypatch):
        """A user file NOT listed in OBSOLETE_FILES and not in manifest → untouched.

        The safety guarantee is: files not enumerated in OBSOLETE_FILES are never
        inspected. Auto-inject only fires on OBSOLETE_FILES entries; paths outside
        that list remain fully protected regardless of manifest state.
        """
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", ["some/obsolete.txt"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        # This file is NOT in OBSOLETE_FILES — cleanup must not even look at it.
        target = tmp_path / "user.txt"
        target.write_text("user file, not ours")
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert report["removed_files"] == []
        assert target.exists()
        assert target.read_text() == "user file, not ours"
        assert not (tmp_path / ".claude" / ".upgrades").exists()

    def test_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → report populated, disk unchanged."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", ["foo.txt"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", ["old_dir"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        (tmp_path / "foo.txt").write_text("obsolete")
        (tmp_path / "old_dir").mkdir()
        (tmp_path / "old_dir" / "nested.txt").write_text("data")
        manifest = {"files": {"foo.txt": "sha256:abc"}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=True)

        assert "foo.txt" in report["removed_files"]
        assert "old_dir" in report["removed_dirs"]
        assert report["backups"][0] is None
        # Nothing removed on disk
        assert (tmp_path / "foo.txt").exists()
        assert (tmp_path / "old_dir").exists()
        assert not (tmp_path / ".claude" / ".upgrades").exists()
        # Manifest unchanged
        assert manifest["files"] == {"foo.txt": "sha256:abc"}

    def test_strips_settings_hooks(self, tmp_path, monkeypatch):
        """Hook with matching command substring gets stripped, original backed up."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", ["memory-capture.cjs"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "node .claude/hooks/memory-capture.cjs"}]},
                    {"matcher": "Edit", "hooks": [{"type": "command", "command": "node .claude/hooks/keep.cjs"}]},
                ]
            }
        }))
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert len(report["stripped_settings_hooks"]) == 1
        assert "memory-capture.cjs" in report["stripped_settings_hooks"][0]
        # Settings file updated
        updated = json.loads(settings.read_text())
        commands = [h["hooks"][0]["command"] for h in updated["hooks"]["PostToolUse"]]
        assert commands == ["node .claude/hooks/keep.cjs"]
        # Backup exists
        backup_root = Path(report["backups"][0])
        assert (backup_root / "obsolete" / "settings.json").exists()

    def test_removes_manifest_dir_with_nested_entries(self, tmp_path, monkeypatch):
        """Directory removal also strips matching manifest entries."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [".beads/memory"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        mem_dir = tmp_path / ".beads" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "knowledge.jsonl").write_text("")
        (mem_dir / "recall.cjs").write_text("// old")
        manifest = {"files": {".beads/memory/recall.cjs": "sha256:x", "other.md": "sha256:y"}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert ".beads/memory" in report["removed_dirs"]
        assert not mem_dir.exists()
        assert ".beads/memory/recall.cjs" not in manifest["files"]
        assert "other.md" in manifest["files"]

    def test_rejects_relative_traversal(self, tmp_path, monkeypatch, capsys):
        """OBSOLETE_FILES entry with ../ → skipped, external file untouched, no backup."""
        # project_dir must be a subdir of tmp_path so `../escape.txt` lands in tmp_path
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        external = tmp_path / "escape.txt"
        external.write_text("external content — do not touch")

        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", ["../escape.txt"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        manifest = {"files": {"../escape.txt": "sha256:abc"}}

        try:
            report = cleanup_obsolete(project_dir, manifest, dry_run=False)

            assert report["removed_files"] == []
            # External file still exists, content unchanged
            assert external.exists()
            assert external.read_text() == "external content — do not touch"
            # Manifest entry not removed
            assert "../escape.txt" in manifest["files"]
            # No backup dir was created
            assert not (project_dir / ".claude" / ".upgrades").exists()
            # Warning printed
            out = capsys.readouterr().out
            assert "Skipping suspicious path" in out
        finally:
            if external.exists():
                external.unlink()

    def test_rejects_absolute_path_outside_project(self, tmp_path, monkeypatch, capsys):
        """OBSOLETE_FILES entry with absolute path outside project_dir → skipped."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("outside content")

        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [str(outside)])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        manifest = {"files": {str(outside): "sha256:abc"}}

        try:
            report = cleanup_obsolete(project_dir, manifest, dry_run=False)

            assert report["removed_files"] == []
            assert outside.exists()
            assert outside.read_text() == "outside content"
            assert str(outside) in manifest["files"]
            assert not (project_dir / ".claude" / ".upgrades").exists()
            out = capsys.readouterr().out
            assert "Skipping suspicious path" in out
        finally:
            if outside.exists():
                outside.unlink()

    def test_rejects_traversal_for_dirs(self, tmp_path, monkeypatch, capsys):
        """OBSOLETE_DIRS entry with ../ → skipped, external dir untouched, no backup."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        external_dir = tmp_path / "escape_dir"
        external_dir.mkdir()
        (external_dir / "nested.txt").write_text("nested")

        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", ["../escape_dir"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        manifest = {"files": {}}

        try:
            report = cleanup_obsolete(project_dir, manifest, dry_run=False)

            assert report["removed_dirs"] == []
            # External dir + its contents untouched
            assert external_dir.exists()
            assert (external_dir / "nested.txt").exists()
            assert (external_dir / "nested.txt").read_text() == "nested"
            # No backup dir was created
            assert not (project_dir / ".claude" / ".upgrades").exists()
            out = capsys.readouterr().out
            assert "Skipping suspicious path" in out
        finally:
            if external_dir.exists():
                import shutil as _sh
                _sh.rmtree(external_dir)


# ============================================================================
# bd-3 logic: legacy auto-inject, knowledge.jsonl guard, empty-settings cleanup
# ============================================================================

class TestBd3Logic:
    # --- _auto_inject_legacy_files --------------------------------------

    def test_auto_inject_legacy_files_adds_existing_unmanaged(self, tmp_path, monkeypatch):
        """File exists on disk, not in manifest → injected with sentinel hash."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [".claude/hooks/memory-capture.cjs"])
        target = tmp_path / ".claude" / "hooks" / "memory-capture.cjs"
        target.parent.mkdir(parents=True)
        target.write_text("// legacy")
        manifest = {"files": {}}

        injected = _auto_inject_legacy_files(tmp_path, manifest, dry_run=False)

        assert injected == [".claude/hooks/memory-capture.cjs"]
        assert manifest["files"][".claude/hooks/memory-capture.cjs"] == "sha256:legacy-auto-injected"

    def test_auto_inject_legacy_files_skips_missing(self, tmp_path, monkeypatch):
        """Path not on disk → not injected."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [".claude/hooks/memory-capture.cjs"])
        manifest = {"files": {}}

        injected = _auto_inject_legacy_files(tmp_path, manifest, dry_run=False)

        assert injected == []
        assert manifest["files"] == {}

    def test_auto_inject_legacy_files_skips_already_in_manifest(self, tmp_path, monkeypatch):
        """Path already a manifest key → not touched."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [".claude/hooks/memory-capture.cjs"])
        target = tmp_path / ".claude" / "hooks" / "memory-capture.cjs"
        target.parent.mkdir(parents=True)
        target.write_text("// legacy")
        manifest = {"files": {".claude/hooks/memory-capture.cjs": "sha256:real-hash"}}

        injected = _auto_inject_legacy_files(tmp_path, manifest, dry_run=False)

        assert injected == []
        # Original hash preserved
        assert manifest["files"][".claude/hooks/memory-capture.cjs"] == "sha256:real-hash"

    def test_auto_inject_dry_run_does_not_mutate(self, tmp_path, monkeypatch):
        """dry_run=True → manifest unchanged, but result still reports what would be injected."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [".claude/hooks/memory-capture.cjs"])
        target = tmp_path / ".claude" / "hooks" / "memory-capture.cjs"
        target.parent.mkdir(parents=True)
        target.write_text("// legacy")
        manifest = {"files": {}}

        injected = _auto_inject_legacy_files(tmp_path, manifest, dry_run=True)

        assert injected == [".claude/hooks/memory-capture.cjs"]
        assert manifest["files"] == {}

    # --- _memory_dir_should_skip ----------------------------------------

    def test_memory_dir_skipped_if_knowledge_nonempty(self, tmp_path, monkeypatch):
        """Non-empty knowledge.jsonl → .beads/memory preserved, report.skipped_dirs populated."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [".beads/memory"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        mem_dir = tmp_path / ".beads" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "knowledge.jsonl").write_text("data\n")
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert mem_dir.exists()
        assert (mem_dir / "knowledge.jsonl").exists()
        assert report["removed_dirs"] == []
        assert len(report["skipped_dirs"]) == 1
        rel, reason = report["skipped_dirs"][0]
        assert rel == ".beads/memory"
        assert "knowledge.jsonl" in reason

    def test_memory_dir_removed_if_knowledge_empty(self, tmp_path, monkeypatch):
        """Empty (0-byte) knowledge.jsonl → dir removed normally."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [".beads/memory"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        mem_dir = tmp_path / ".beads" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "knowledge.jsonl").write_text("")
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert not mem_dir.exists()
        assert ".beads/memory" in report["removed_dirs"]
        assert report["skipped_dirs"] == []

    def test_memory_dir_removed_if_knowledge_missing(self, tmp_path, monkeypatch):
        """No knowledge.jsonl at all → dir removed normally."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [".beads/memory"])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        mem_dir = tmp_path / ".beads" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "filler.cjs").write_text("// other")
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert not mem_dir.exists()
        assert ".beads/memory" in report["removed_dirs"]
        assert report["skipped_dirs"] == []

    # --- _cleanup_empty_local_settings ----------------------------------

    def test_cleanup_empty_local_settings_removes_file(self, tmp_path, monkeypatch):
        """settings.local.json with only empty hook lists → file deleted."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.local.json"
        settings.write_text(json.dumps({"hooks": {"SessionStart": []}}))
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert not settings.exists()
        assert report["removed_local_settings"] is True
        # Backup was made
        backup_root = Path(report["backups"][0])
        assert (backup_root / "obsolete" / ".claude" / "settings.local.json").exists()

    def test_cleanup_empty_local_settings_keeps_if_other_hooks(self, tmp_path, monkeypatch):
        """settings.local.json still has real hook entries → file kept."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.local.json"
        settings.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "echo hi"}]},
                ]
            }
        }))
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=False)

        assert settings.exists()
        assert report["removed_local_settings"] is False

    def test_cleanup_empty_local_settings_dry_run(self, tmp_path, monkeypatch):
        """dry_run=True → report says True but file untouched."""
        monkeypatch.setattr(bootstrap, "OBSOLETE_FILES", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_DIRS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_SETTINGS_HOOKS", [])
        monkeypatch.setattr(bootstrap, "OBSOLETE_LOCAL_SETTINGS_PATTERNS", [])

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.local.json"
        settings.write_text(json.dumps({"hooks": {"SessionStart": []}}))
        manifest = {"files": {}}

        report = cleanup_obsolete(tmp_path, manifest, dry_run=True)

        assert settings.exists()
        assert report["removed_local_settings"] is True

    def test_cleanup_empty_local_settings_missing_file(self, tmp_path):
        """File absent → no-op, helper returns False."""
        result = _cleanup_empty_local_settings(
            tmp_path, lambda: tmp_path / ".bk", dry_run=False,
        )
        assert result is False


# ============================================================================
# main() flags: --upgrade, --all
# ============================================================================

class TestUpgradeFlag:
    def test_upgrade_flag_calls_cleanup(self, tmp_path, monkeypatch):
        """main() with --upgrade invokes cleanup_obsolete when manifest exists."""
        # Seed manifest so upgrade path runs
        save_manifest(tmp_path, {"version": "3.0.0", "installed_at": "t", "files": {}})

        calls = []

        def fake_cleanup(project_dir, manifest, dry_run, timestamp=None):
            calls.append({"project_dir": project_dir, "dry_run": dry_run})
            return {
                "removed_files": [], "removed_dirs": [],
                "stripped_settings_hooks": [], "stripped_local_patterns": [],
                "backups": [None],
            }

        # Stub out heavy steps so test stays fast & offline
        monkeypatch.setattr(bootstrap, "cleanup_obsolete", fake_cleanup)
        monkeypatch.setattr(bootstrap, "install_beads", lambda pd, dry_run=False: True)
        monkeypatch.setattr(bootstrap, "copy_agents", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_hooks", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "copy_rules_and_skills", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_settings_and_claude_md", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "setup_gitignore", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "run_bd_doctor", lambda *a, **kw: None)

        monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--project-dir", str(tmp_path), "--upgrade"])
        with pytest.raises(SystemExit) as exc:
            bootstrap.main()
        assert exc.value.code == 0
        assert len(calls) == 1
        assert calls[0]["dry_run"] is False

    def test_upgrade_runs_cleanup_without_manifest(self, tmp_path, monkeypatch):
        """--upgrade must still run cleanup_obsolete for legacy installs (no
        manifest). _auto_inject_legacy_files handles the no-manifest case;
        skipping cleanup would leave pre-manifest OBSOLETE_* files on disk."""
        calls = []

        def fake_cleanup(*args, **kw):
            calls.append(args)
            return {
                "removed_files": [], "removed_dirs": [],
                "stripped_settings_hooks": [], "stripped_local_patterns": [],
                "backups": [None],
            }

        monkeypatch.setattr(bootstrap, "cleanup_obsolete", fake_cleanup)
        monkeypatch.setattr(bootstrap, "install_beads", lambda pd, dry_run=False: True)
        monkeypatch.setattr(bootstrap, "copy_agents", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_hooks", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "copy_rules_and_skills", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_settings_and_claude_md", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "setup_gitignore", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "run_bd_doctor", lambda *a, **kw: None)

        monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--project-dir", str(tmp_path), "--upgrade"])
        with pytest.raises(SystemExit) as exc:
            bootstrap.main()
        assert exc.value.code == 0
        assert len(calls) == 1

    def test_recorder_shares_upgrade_folder_with_cleanup(self, tmp_path, monkeypatch):
        """Recoder + cleanup_obsolete must share .claude/.upgrades/<ts>/ so
        both overwritten/ (recorder) and obsolete/ (cleanup) land in one
        folder. Regression for the c18327a wiring."""
        # Seed a manifest so upgrade path runs.
        save_manifest(tmp_path, {"version": "3.0.0", "installed_at": "t", "files": {}})

        captured: dict = {}

        def capture_cleanup(project_dir, manifest, dry_run, timestamp=None):
            captured["timestamp"] = timestamp
            return {
                "removed_files": [], "removed_dirs": [],
                "stripped_settings_hooks": [], "stripped_local_patterns": [],
                "removed_local_settings": False, "skipped_dirs": [],
                "legacy_injected": [], "backups": [None],
            }

        monkeypatch.setattr(bootstrap, "cleanup_obsolete", capture_cleanup)
        monkeypatch.setattr(bootstrap, "install_beads", lambda pd, dry_run=False: True)
        monkeypatch.setattr(bootstrap, "copy_agents", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_hooks", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "copy_rules_and_skills", lambda *a, **kw: [])
        monkeypatch.setattr(bootstrap, "copy_settings_and_claude_md", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "setup_gitignore", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "run_bd_doctor", lambda *a, **kw: None)

        bootstrap.bootstrap_project(
            project_dir=tmp_path, project_name="P", with_rules=False,
            force=False, upgrade=True, dry_run=True,
        )

        # Build a recorder for the same dir; the timestamp it picks must
        # match what was passed to cleanup_obsolete.
        rec = bootstrap.ChangeRecorder(tmp_path)
        assert captured["timestamp"] == rec.timestamp
        assert captured["timestamp"] is not None


class TestAllFlag:
    def test_iterates_subdirs_with_beads(self, tmp_path, monkeypatch):
        """--all <parent> processes direct subdirs containing .beads/, skips others."""
        parent = tmp_path / "workspace"
        parent.mkdir()
        good1 = parent / "proj_a"
        good1.mkdir()
        (good1 / ".beads").mkdir()
        good2 = parent / "proj_b"
        good2.mkdir()
        (good2 / ".beads").mkdir()
        bad = parent / "proj_c"
        bad.mkdir()  # no .beads/
        # file (not a directory) — must not break iteration
        (parent / "stray.txt").write_text("")

        processed: list = []

        def fake_bootstrap_project(**kwargs):
            processed.append(kwargs["project_dir"])
            return 0

        monkeypatch.setattr(bootstrap, "bootstrap_project", fake_bootstrap_project)

        monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--all", str(parent)])
        with pytest.raises(SystemExit) as exc:
            bootstrap.main()
        assert exc.value.code == 0
        names = sorted(p.name for p in processed)
        assert names == ["proj_a", "proj_b"]

    def test_missing_parent_dir_fails_cleanly(self, tmp_path, monkeypatch):
        """--all with a non-existent parent returns exit 1."""
        missing = tmp_path / "does_not_exist"
        monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--all", str(missing)])
        with pytest.raises(SystemExit) as exc:
            bootstrap.main()
        assert exc.value.code == 1


class TestBootstrapProjectErrorHandling:
    def test_mid_step_failure_still_reports_and_saves_manifest(
        self, tmp_path, monkeypatch, capsys,
    ):
        """If a sub-step raises after some put_file calls have succeeded,
        bootstrap_project must (a) print the [CHANGES] report so the user
        sees what landed and (b) save_manifest so the next run doesn't
        churn through those files as 'modified'.

        Regression for the silent-orphan pattern: without the try/except
        wrapper, the user sees a Python traceback and the manifest on
        disk is stale, so the next run re-backs-up everything.
        """
        # Two successful put_file calls, then a third sub-step raises.
        def fake_copy_agents(recorder, project_name):
            dest = tmp_path / ".claude" / "agents" / "a.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            recorder.put_file(dest, b"agent content\n", "agents/a.md")
            return []

        def fake_copy_hooks(recorder):
            dest = tmp_path / ".claude" / "hooks" / "h.cjs"
            dest.parent.mkdir(parents=True, exist_ok=True)
            recorder.put_file(dest, b"hook content\n", "hooks/h.cjs")

        def boom(recorder, with_rules):
            raise RuntimeError("simulated mid-step failure")

        monkeypatch.setattr(bootstrap, "install_beads", lambda pd, dry_run=False: True)
        monkeypatch.setattr(bootstrap, "copy_agents", fake_copy_agents)
        monkeypatch.setattr(bootstrap, "copy_hooks", fake_copy_hooks)
        monkeypatch.setattr(bootstrap, "copy_rules_and_skills", boom)
        monkeypatch.setattr(
            bootstrap, "copy_settings_and_claude_md", lambda *a, **kw: None,
        )
        monkeypatch.setattr(bootstrap, "setup_gitignore", lambda *a, **kw: None)
        monkeypatch.setattr(bootstrap, "run_bd_doctor", lambda *a, **kw: None)

        rc = bootstrap.bootstrap_project(
            project_dir=tmp_path, project_name="P", with_rules=False,
            force=False, upgrade=False, dry_run=False,
        )

        assert rc == 1
        out = capsys.readouterr().out
        assert "2 files changed" in out  # report still printed on failure
        assert "agents/a.md" in out  # the two successful writes are visible

        # Manifest was saved — next run sees the new files as 'pristine'.
        manifest = bootstrap.load_manifest(tmp_path)
        assert "agents/a.md" in manifest["files"]
        assert "hooks/h.cjs" in manifest["files"]


class TestBdDoctorSoftFailure:
    def test_missing_bd_is_soft_failure(self, tmp_path, monkeypatch, capsys):
        """bd not on PATH → prints warning, does not raise."""
        monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)
        # Must not raise
        run_bd_doctor(tmp_path)
        out = capsys.readouterr().out
        assert "bd doctor unavailable" in out

    def test_nonzero_exit_is_soft_failure(self, tmp_path, monkeypatch, capsys):
        """bd doctor returning non-zero → prints warning, does not raise."""
        monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/bd")

        class FakeResult:
            returncode = 2
            stdout = ""
            stderr = "no dolt server\n"

        monkeypatch.setattr(
            bootstrap.subprocess, "run",
            lambda *a, **kw: FakeResult(),
        )
        run_bd_doctor(tmp_path)
        out = capsys.readouterr().out
        assert "bd doctor unavailable" in out

    def test_timeout_is_soft_failure(self, tmp_path, monkeypatch, capsys):
        """bd doctor timeout → prints warning, does not raise."""
        monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/bd")

        def fake_run(*a, **kw):
            raise bootstrap.subprocess.TimeoutExpired(cmd="bd", timeout=15)

        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
        run_bd_doctor(tmp_path)
        out = capsys.readouterr().out
        assert "bd doctor unavailable" in out

    def test_success_prints_first_lines(self, tmp_path, monkeypatch, capsys):
        """Successful bd doctor → first 20 lines of stdout printed under header."""
        monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/bd")

        class FakeResult:
            returncode = 0
            stdout = "\n".join(f"line {i}" for i in range(30))
            stderr = ""

        monkeypatch.setattr(
            bootstrap.subprocess, "run",
            lambda *a, **kw: FakeResult(),
        )
        run_bd_doctor(tmp_path)
        out = capsys.readouterr().out
        assert "bd doctor:" in out
        assert "line 0" in out
        assert "line 19" in out
        assert "line 20" not in out  # Truncated at 20


# ============================================================================
# settings merge must preserve bd's own SessionStart hook
# ============================================================================

def _fake_templates_dir(root: Path) -> Path:
    """Write a minimal templates/ dir (hermetic — independent of the real one)."""
    templates = root / "fake_templates"
    templates.mkdir(parents=True)
    settings = {
        "hooks": {"SessionStart": [
            {"hooks": [{"command": "node .claude/hooks/session-start.cjs",
                        "type": "command"}],
             "matcher": ""}
        ]}
    }
    (templates / "settings.json").write_text(json.dumps(settings))
    (templates / "CLAUDE.md").write_text(
        "# [Project]\n\nORCHESTRATION TEMPLATE BODY\n"
    )
    return templates


class TestSettingsMergePreservesBdHook:
    def test_bd_prime_hook_survives_merge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bootstrap, "TEMPLATES_DIR",
                            _fake_templates_dir(tmp_path))
        # Simulate what `bd init` wrote first.
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        bd_settings = {
            "hooks": {"SessionStart": [
                {"hooks": [{"command": "bd prime --hook-json", "type": "command"}],
                 "matcher": ""}
            ]}
        }
        (settings_dir / "settings.json").write_text(json.dumps(bd_settings))

        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")

        merged = json.loads((settings_dir / "settings.json").read_text())
        cmds = [h["hooks"][0]["command"] for h in merged["hooks"]["SessionStart"]]
        assert "bd prime --hook-json" in cmds  # bd's hook preserved
        assert any("session-start.cjs" in c for c in cmds)  # ours added too


class TestSettingsParseFailureBackup:
    def test_unparseable_settings_backed_up_then_replaced(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(bootstrap, "TEMPLATES_DIR", _fake_templates_dir(tmp_path))
        (tmp_path / ".claude").mkdir(parents=True)
        broken = tmp_path / ".claude" / "settings.json"
        broken.write_text("{ this is not valid json ")
        rec = bootstrap.ChangeRecorder(tmp_path)
        bootstrap.copy_settings_and_claude_md(rec, "Proj")
        # valid JSON written
        assert "hooks" in json.loads(broken.read_text())
        # broken original backed up byte-exact
        backup = rec.backup_root / "overwritten" / ".claude" / "settings.json"
        assert backup.read_text() == "{ this is not valid json "
        # merge-failure surfaced in the report, not silently replaced
        rec.print_report()
        assert "could not merge — replaced" in capsys.readouterr().out


class TestClaudeMdAppendIdempotent:
    def test_orchestration_appended_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bootstrap, "TEMPLATES_DIR",
                            _fake_templates_dir(tmp_path))
        (tmp_path / ".claude").mkdir()
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# Project\n\n<!-- BEGIN BEADS INTEGRATION -->\nbd block\n")

        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")
        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")

        content = claude.read_text()
        assert content.count("<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->") == 1
        assert "<!-- BEGIN BEADS INTEGRATION -->" in content  # bd's block preserved
        assert content.count("ORCHESTRATION TEMPLATE BODY") == 1  # body not duplicated

    def test_create_path_idempotent(self, tmp_path, monkeypatch):
        """No CLAUDE.md yet: create on first run, do not duplicate on the second."""
        monkeypatch.setattr(bootstrap, "TEMPLATES_DIR",
                            _fake_templates_dir(tmp_path))
        (tmp_path / ".claude").mkdir()
        claude = tmp_path / "CLAUDE.md"
        assert not claude.exists()

        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")
        bootstrap.copy_settings_and_claude_md(bootstrap.ChangeRecorder(tmp_path), "Proj")

        content = claude.read_text()
        assert content.count("<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->") == 1
        assert content.count("ORCHESTRATION TEMPLATE BODY") == 1  # body not duplicated


# ============================================================================
# ChangeRecorder: backup + atomic write + diff
# ============================================================================

class TestChangeRecorder:
    def _rec(self, tmp_path, **kw):
        return bootstrap.ChangeRecorder(tmp_path, {"files": {}}, **kw)

    def test_force_backs_up_locally_modified(self, tmp_path):
        rec = bootstrap.ChangeRecorder(
            tmp_path, {"files": {"hooks/x.cjs": "sha256:doesnotmatch"}}, force=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"user changed this\n")
        rec.put_file(dest, b"new template\n", "hooks/x.cjs")
        backup = rec.backup_root / "overwritten" / ".claude" / "hooks" / "x.cjs"
        assert backup.read_bytes() == b"user changed this\n"
        assert rec.changes[-1]["label"] == "locally-modified"

    def test_atomic_write_failure_preserves_original(self, tmp_path, monkeypatch):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"original\n")
        rec.manifest["files"]["hooks/x.cjs"] = bootstrap.bytes_sha256(b"original\n")
        def boom(src, dst):
            raise OSError("disk full")
        monkeypatch.setattr(bootstrap.os, "replace", boom)
        with pytest.raises(OSError):
            rec.put_file(dest, b"new\n", "hooks/x.cjs")
        assert dest.read_bytes() == b"original\n"  # original intact
        leftovers = [p.name for p in dest.parent.iterdir() if p.name.startswith(".cp-tmp-")]
        assert leftovers == []  # temp cleaned up
        assert rec.manifest["files"]["hooks/x.cjs"] == bootstrap.bytes_sha256(b"original\n")  # not mutated

    def test_atomic_write_failure_records_attempt_with_backup(self, tmp_path, monkeypatch):
        """When _do_backup succeeds but _atomic_write fails, the changes
        list must still record the attempted overwrite with its backup
        path so the user sees the audit trail in print_report.

        Without this, RES-4: backup is on disk, manifest unchanged, but
        no entry in recorder.changes → print_report is silent about it.
        """
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"original\n")
        rec.manifest["files"]["hooks/x.cjs"] = bootstrap.bytes_sha256(b"original\n")

        def boom(_dest, _data):
            raise OSError("disk full mid-write")
        monkeypatch.setattr(rec, "_atomic_write", boom)

        with pytest.raises(OSError):
            rec.put_file(dest, b"new\n", "hooks/x.cjs")

        # Original intact
        assert dest.read_bytes() == b"original\n"
        # Backup on disk
        backup = rec.backup_root / "overwritten" / ".claude" / "hooks" / "x.cjs"
        assert backup.read_bytes() == b"original\n"
        # Manifest NOT mutated (write didn't succeed)
        assert rec.manifest["files"]["hooks/x.cjs"] == bootstrap.bytes_sha256(b"original\n")
        # And the change IS recorded so the user can see it failed
        assert len(rec.changes) == 1
        assert rec.changes[0]["action"] == "overwritten"
        assert rec.changes[0]["backup"] == backup
        assert rec.changes[0]["key"] == "hooks/x.cjs"

    def test_new_file_written_no_backup(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        action = rec.put_file(dest, b"hello\n", "hooks/x.cjs")
        assert action == "new"
        assert dest.read_bytes() == b"hello\n"
        assert not (rec.backup_root / "overwritten").exists()

    def test_unchanged_is_noop(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / "f.txt"
        dest.write_bytes(b"same\n")
        assert rec.put_file(dest, b"same\n", "f.txt") == "unchanged"
        assert not (rec.backup_root).exists()
        assert rec.changes == []

    def test_overwrite_backs_up_byte_exact(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old\r\nline\r\n")  # CRLF must be preserved in backup
        rec.put_file(dest, b"new\n", "hooks/x.cjs")
        backup = rec.backup_root / "overwritten" / ".claude" / "hooks" / "x.cjs"
        assert backup.read_bytes() == b"old\r\nline\r\n"
        assert dest.read_bytes() == b"new\n"

    def test_label_pristine_vs_locally_modified(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "rules" / "r.md"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"shipped\n")
        rec.manifest["files"]["rules/r.md"] = bootstrap.bytes_sha256(b"shipped\n")
        rec.put_file(dest, b"v2\n", "rules/r.md")
        assert rec.changes[-1]["label"] == "pristine"

        dest.write_bytes(b"user edit\n")
        rec.put_file(dest, b"v3\n", "rules/r.md")
        assert rec.changes[-1]["label"] == "locally-modified"

    def test_record_skip_adds_kept_change_no_write(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "rules" / "x.md"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"user edited\n")
        rec.record_skip("rules/x.md")
        assert dest.read_bytes() == b"user edited\n"          # untouched
        kept = rec.changes[-1]
        assert kept["action"] == "kept"
        assert kept["key"] == "rules/x.md"
        assert kept["label"] == "locally-modified"
        assert kept["backup"] is None
        assert not (rec.backup_root / "overwritten").exists()  # no backup dir

    def test_dry_run_writes_nothing(self, tmp_path):
        rec = self._rec(tmp_path, dry_run=True)
        dest = tmp_path / "f.txt"
        dest.write_bytes(b"orig\n")
        rec.put_file(dest, b"changed\n", "f.txt")
        assert dest.read_bytes() == b"orig\n"
        assert not rec.backup_root.exists()
        assert rec.changes[-1]["action"] == "overwritten"  # still recorded for the report

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "a.txt"
        rec.put_file(dest, b"data\n", "a.txt")
        leftovers = [p.name for p in dest.parent.iterdir() if p.name.startswith(".cp-tmp-")]
        assert leftovers == []

    def test_append_mode_no_backup_no_manifest(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / "CLAUDE.md"
        dest.write_bytes(b"existing\n")
        action = rec.put_file(dest, b"existing\nappended\n", "CLAUDE.md", backup=False)
        assert action == "appended"
        assert dest.read_bytes() == b"existing\nappended\n"
        assert not (rec.backup_root / "overwritten").exists()
        assert "CLAUDE.md" not in rec.manifest["files"]

    def test_replace_tree_backs_up_changed_file(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_bytes(b"old skill\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"new skill\n")

        rec.replace_tree(dest, src, "skills/project-discovery")

        assert (dest / "SKILL.md").read_bytes() == b"new skill\n"
        backup = (rec.backup_root / "overwritten" / ".claude" / "skills"
                  / "project-discovery" / "SKILL.md")
        assert backup.read_bytes() == b"old skill\n"
        actions = {c["key"]: c["action"] for c in rec.changes}
        assert actions["skills/project-discovery/SKILL.md"] == "overwritten"

    def test_replace_tree_records_new_when_no_dest(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"fresh\n")
        rec.replace_tree(dest, src, "skills/project-discovery")
        assert (dest / "SKILL.md").read_bytes() == b"fresh\n"
        assert rec.changes[-1]["action"] == "new"

    def test_replace_tree_dry_run_no_disk_changes(self, tmp_path):
        rec = self._rec(tmp_path, dry_run=True)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_bytes(b"keep\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"new\n")
        rec.replace_tree(dest, src, "skills/project-discovery")
        assert (dest / "SKILL.md").read_bytes() == b"keep\n"   # not replaced
        assert not rec.backup_root.exists()
        assert rec.changes[-1]["action"] == "overwritten"       # still recorded

    def test_replace_tree_removed_file_backed_up_and_dekeyed(self, tmp_path):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "skills" / "project-discovery"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_bytes(b"keep\n")
        (dest / "OLD.md").write_bytes(b"gone\n")
        rec.manifest["files"]["skills/project-discovery/OLD.md"] = "sha256:stale"
        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_bytes(b"keep2\n")
        rec.replace_tree(dest, src, "skills/project-discovery")
        assert not (dest / "OLD.md").exists()
        removed = [c for c in rec.changes if c["key"].endswith("OLD.md")][0]
        assert removed["action"] == "removed"
        backup = (rec.backup_root / "overwritten" / ".claude" / "skills"
                  / "project-discovery" / "OLD.md")
        assert backup.read_bytes() == b"gone\n"
        assert "skills/project-discovery/OLD.md" not in rec.manifest["files"]
        assert "skills/project-discovery/SKILL.md" in rec.manifest["files"]

    def test_report_shows_summary_and_diff(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"line1\nline2\n")
        rec.put_file(dest, b"line1\nCHANGED\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "1 file changed" in out
        assert "UPDATE" in out
        assert "hooks/x.cjs" in out
        assert "+1 -1" in out
        assert "\n-line2\n" in out and "\n+CHANGED\n" in out  # full diff present

    def test_report_no_diff_suppresses_full_diff(self, tmp_path, capsys):
        rec = self._rec(tmp_path, no_diff=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"line1\nline2\n")
        rec.put_file(dest, b"line1\nCHANGED\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "hooks/x.cjs" in out          # summary still shown
        assert "+CHANGED" not in out          # full diff suppressed

    def test_report_empty_says_no_changes(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        rec.print_report()
        out = capsys.readouterr().out
        assert "No changes" in out

    def test_report_dry_run_prefix(self, tmp_path, capsys):
        rec = self._rec(tmp_path, dry_run=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"a\n")
        rec.put_file(dest, b"b\n", "hooks/x.cjs")
        rec.print_report()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "would change" in out
        assert "backup:" not in out  # no backup line in dry-run

    def test_report_dry_run_empty_says_no_changes(self, tmp_path, capsys):
        rec = self._rec(tmp_path, dry_run=True)
        rec.print_report()
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "no changes" in out

    def test_report_kept_section_lists_user_modified(self, tmp_path, capsys):
        rec = self._rec(tmp_path)
        rec.record_skip("rules/beads-workflow.md")
        rec.record_skip("rules/debugging-standard.md")
        rec.print_report()
        out = capsys.readouterr().out
        assert "KEPT" in out
        assert "rules/beads-workflow.md" in out
        assert "rules/debugging-standard.md" in out
        assert "No changes" in out  # kept does not count as a change

    def test_report_note_only_on_locally_modified(self, tmp_path, capsys):
        rec = bootstrap.ChangeRecorder(
            tmp_path, {"files": {"hooks/x.cjs": "sha256:doesnotmatch"}}, force=True)
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"user edited\n")
        rec.put_file(dest, b"new\n", "hooks/x.cjs")  # label -> locally-modified
        rec.print_report()
        out = capsys.readouterr().out
        assert "your edits will be replaced" in out

    def test_report_pristine_has_no_note(self, tmp_path, capsys):
        rec = bootstrap.ChangeRecorder(
            tmp_path, {"files": {"hooks/x.cjs": bootstrap.content_sha256("old\n")}})
        dest = tmp_path / ".claude" / "hooks" / "x.cjs"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"old\n")
        rec.put_file(dest, b"new\n", "hooks/x.cjs")  # label -> pristine
        rec.print_report()
        out = capsys.readouterr().out
        assert "your edits will be replaced" not in out


class TestSummarizeChanges:
    def _c(self, action):
        return {"action": action, "key": "k", "label": None,
                "added": 0, "removed": 0, "diff": [], "backup": None}

    def test_empty_is_no_changes(self):
        assert bootstrap.summarize_changes([]) == "no changes"

    def test_counts_by_verb_in_order(self):
        slice_ = [self._c("new"), self._c("overwritten"), self._c("overwritten"),
                  self._c("appended"), self._c("kept"), self._c("kept")]
        assert bootstrap.summarize_changes(slice_) == "1 new · 2 updated · 1 appended · 2 kept"

    def test_only_updated(self):
        assert bootstrap.summarize_changes([self._c("overwritten")]) == "1 updated"


# ============================================================================
# ANSI color
# ============================================================================

class TestColor:
    @pytest.fixture(autouse=True)
    def _reset_color(self):
        yield
        bootstrap.configure_color("never")  # never leak color state to other tests

    def test_paint_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", False)
        assert bootstrap._paint("hi", "green") == "hi"

    def test_paint_wraps_when_enabled(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", True)
        out = bootstrap._paint("hi", "green")
        assert out == "\033[32mhi\033[0m"

    def test_paint_noop_without_styles(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", True)
        assert bootstrap._paint("hi") == "hi"  # no stray reset code

    def test_diff_line_colors(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", True)
        assert bootstrap._color_diff_line("+added").startswith("\033[32m")     # green
        assert bootstrap._color_diff_line("-gone").startswith("\033[31m")      # red
        assert bootstrap._color_diff_line("@@ -1 +1 @@").startswith("\033[36m")  # cyan
        assert bootstrap._color_diff_line("+++ b/x").startswith("\033[1m")     # bold header
        assert bootstrap._color_diff_line("--- a/x").startswith("\033[1m")     # bold header
        assert bootstrap._color_diff_line(" ctx") == " ctx"                    # untouched

    def test_diff_line_plain_when_disabled(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", False)
        assert bootstrap._color_diff_line("+added") == "+added"

    def test_configure_color_always_and_never(self):
        bootstrap.configure_color("always")
        assert bootstrap._COLOR_ENABLED is True
        bootstrap.configure_color("never")
        assert bootstrap._COLOR_ENABLED is False

    def test_auto_off_under_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        bootstrap.configure_color("auto")
        assert bootstrap._COLOR_ENABLED is False

    def test_auto_off_when_not_a_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        bootstrap.configure_color("auto")  # pytest stdout is not a tty
        assert bootstrap._COLOR_ENABLED is False

    def test_report_line_colors_verb_and_counts(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", True)
        c = {"action": "overwritten", "key": "hooks/x.cjs",
             "added": 6, "removed": 48, "label": "pristine"}
        line = bootstrap.ChangeRecorder._report_line(c, 12)
        assert "\033[33m" in line  # UPDATE yellow
        assert "\033[32m" in line  # +6 green
        assert "\033[31m" in line  # -48 red

    def test_report_line_byte_identical_when_color_off(self, monkeypatch):
        monkeypatch.setattr(bootstrap, "_COLOR_ENABLED", False)
        c = {"action": "overwritten", "key": "hooks/x.cjs",
             "added": 6, "removed": 48, "label": "pristine"}
        line = bootstrap.ChangeRecorder._report_line(c, 12)
        assert "\033[" not in line
        assert line == "  UPDATE  hooks/x.cjs    +6 -48"
