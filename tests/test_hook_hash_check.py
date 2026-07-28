"""Tests for the F-04 supply-chain hardening in copy_hooks().

Validates that:
1. When a shipped .cjs hook template's SHA-256 differs from
   ``_EXPECTED_HOOK_HASHES``, ``copy_hooks()`` raises RuntimeError and does
   NOT install the hook.
2. When ``allow_untouched=True`` is passed, the same tampered hook is
   installed anyway (with a warning).
3. Normal (untampered) hooks install silently.
"""

import hashlib

import pytest

import bootstrap


def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_copy_hooks_installs_when_all_hashes_match(tmp_path):
    """Happy path: shipped templates match the expected hash table."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    templates_dir = bootstrap.TEMPLATES_DIR

    # Sanity: every hook in the shipped set matches the table.
    for hook_name, expected in bootstrap._EXPECTED_HOOK_HASHES.items():
        actual = _hash_file(templates_dir / "hooks" / hook_name)
        assert actual == expected, (
            f"Test pre-condition failed: {hook_name} hash mismatch — "
            f"the hash table is out of sync with templates/hooks/. "
            f"Run `sha256sum templates/hooks/*.cjs` and update the constant."
        )

    recorder = bootstrap.ChangeRecorder(project_dir=project_dir, force=False, dry_run=False)
    bootstrap.copy_hooks(recorder)
    # All expected hooks are now in the project's .claude/hooks/ dir.
    installed = sorted(p.name for p in (project_dir / ".claude" / "hooks").iterdir())
    assert set(installed) == set(bootstrap._EXPECTED_HOOK_HASHES.keys())


def _setup_fake_template_with_one_tampered_hook(
    tmp_path, monkeypatch, tampered_name="bash-guard.cjs"
):
    """Build a fake templates/hooks/ dir that contains a tampered copy of
    ``tampered_name`` plus a *different* sentinel hook whose hash IS in the
    table. Returns (project_dir, patched_hashes)."""
    fake_templates = tmp_path / "fake-templates"
    fake_templates.mkdir()
    fake_hooks = fake_templates / "hooks"
    fake_hooks.mkdir()

    # The tampered hook: content deliberately differs from the shipped hash.
    (fake_hooks / tampered_name).write_text(
        "// TAMPERED — this exact byte content triggers the supply-chain abort\n"
    )

    # Pick a sentinel hook name that is NOT the tampered one.
    other_names = [
        name for name in bootstrap._EXPECTED_HOOK_HASHES
        if name != tampered_name
    ]
    sentinel_name = other_names[0]
    sentinel = fake_hooks / sentinel_name
    sentinel.write_text(f"// sentinel for {sentinel_name}\n")
    sentinel_hash = _hash_file(sentinel)

    # Update the expected hash for the sentinel so it passes; keep the
    # tampered hook's expected hash UNCHANGED (still pointing at the real
    # shipped hash), so the tampered file genuinely mismatches.
    patched_hashes = dict(bootstrap._EXPECTED_HOOK_HASHES)
    patched_hashes[sentinel_name] = sentinel_hash

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    monkeypatch.setattr(bootstrap, "TEMPLATES_DIR", fake_templates)
    monkeypatch.setattr(bootstrap, "_EXPECTED_HOOK_HASHES", patched_hashes)
    monkeypatch.setattr(bootstrap, "summarize_changes", lambda changes: "ok")
    return project_dir, patched_hashes


def test_copy_hooks_refuses_tampered_template(tmp_path, monkeypatch):
    """If a shipped hook is edited after we shipped, copy_hooks must abort."""
    project_dir, _ = _setup_fake_template_with_one_tampered_hook(
        tmp_path, monkeypatch, tampered_name="bash-guard.cjs",
    )

    recorder = bootstrap.ChangeRecorder(
        project_dir=project_dir, force=False, dry_run=False,
    )

    with pytest.raises(RuntimeError) as exc_info:
        bootstrap.copy_hooks(recorder)
    msg = str(exc_info.value)
    assert "hash verification" in msg.lower()
    assert "bash-guard.cjs" in msg

    # The tampered hook must NOT have been installed.
    assert not (project_dir / ".claude" / "hooks" / "bash-guard.cjs").exists()


def test_copy_hooks_allow_untouched_installs_tampered_with_warning(
    tmp_path, monkeypatch
):
    """With allow_untouched=True, the tampered hook IS installed (warning)."""
    project_dir, _ = _setup_fake_template_with_one_tampered_hook(
        tmp_path, monkeypatch, tampered_name="bash-guard.cjs",
    )

    recorder = bootstrap.ChangeRecorder(
        project_dir=project_dir, force=False, dry_run=False,
    )
    # No raise this time:
    bootstrap.copy_hooks(recorder, allow_untouched=True)

    # The tampered bash-guard hook IS installed when bypass is active.
    assert (project_dir / ".claude" / "hooks" / "bash-guard.cjs").exists()


# -- F-03: Codex config.toml preservation -----------------------------------

def test_codex_settings_preserves_existing_user_config(tmp_path):
    """Regression (F-03, security audit): _write_settings_for_adapter must
    NOT clobber an existing user-owned .codex/config.toml. The shipped
    Codex template is just a 4-line comment, so any non-comment content
    is treated as user config and left alone.
    """
    from adapters import CODEX

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    codex_dir = project_dir / ".codex"
    codex_dir.mkdir()
    user_config = codex_dir / "config.toml"
    user_config.write_text(
        "# user's real Codex config\n"
        'model = "gpt-5-codex"\n'
        'sandbox = "workspace-write"\n'
        'approval_policy = "on-failure"\n'
    )

    recorder = bootstrap.ChangeRecorder(
        project_dir=project_dir, force=False, dry_run=False,
    )
    bootstrap._write_settings_for_adapter(recorder, CODEX, project_dir)

    # The user file must still be on disk, byte-identical.
    assert user_config.read_text(encoding="utf-8") == (
        "# user's real Codex config\n"
        'model = "gpt-5-codex"\n'
        'sandbox = "workspace-write"\n'
        'approval_policy = "on-failure"\n'
    )
    # And the manifest must record "preserved" so the user sees we noticed.
    preserved = [c for c in recorder.changes if c.get("action") == "preserved"]
    assert len(preserved) == 1
    assert preserved[0]["label"] == "existing user config"


def test_codex_settings_writes_placeholder_when_no_existing_config(tmp_path):
    """When .codex/config.toml does NOT exist, the placeholder is written
    normally (fresh-install behaviour unchanged)."""
    from adapters import CODEX

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    # No .codex/ yet.

    recorder = bootstrap.ChangeRecorder(
        project_dir=project_dir, force=False, dry_run=False,
    )
    bootstrap._write_settings_for_adapter(recorder, CODEX, project_dir)

    # The placeholder should be on disk now.
    assert (project_dir / ".codex" / "config.toml").exists()

