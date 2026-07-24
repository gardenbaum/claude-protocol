"""Harness adapter registry for v4.0.0 multi-harness support.

A :class:`HarnessAdapter` declares where a CLI agent stores its config and
which filename conventions it uses. The bootstrap orchestrator dispatches the
shared install steps (agents, hooks, rules, skills, settings,
agent-instructions) once per adapter, so existing files under ``.claude/``
remain byte-equivalent for the default ``claude`` adapter (v3.8.2 compat).

Adapter layout (data-only; the orchestrator knows how to walk it):

    templates/<harness>/
        agents/<name>.md            # per-harness agents (opencode/omp/...)
        skills/<skill>/SKILL.md     # per-harness skills
        rules/<name>.md             # per-harness rules (omp only)
        plugins/<name>.ts/.js       # opencode plugin entry
        extensions/<name>.ts/.js    # omp extension entry
        shared/runtime-policy.js    # harness-agnostic enforcement policy
        AGENTS.md / CLAUDE.md       # top-level agent instructions

The default ``claude`` adapter stays byte-equivalent: it reuses the
existing ``templates/agents``, ``templates/hooks``, ``templates/rules``,
``templates/skills`` directories plus ``templates/CLAUDE.md`` and
``templates/settings.json`` — no new copy is emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TEMPLATES_DIR = Path(__file__).parent / "templates"
SHARED_AGENTS_DIR = TEMPLATES_DIR / "agents"
SHARED_HOOKS_DIR = TEMPLATES_DIR / "hooks"
SHARED_RULES_DIR = TEMPLATES_DIR / "rules"
SHARED_SKILLS_DIR = TEMPLATES_DIR / "skills"
SHARED_CLAUDE_MD = TEMPLATES_DIR / "CLAUDE.md"
SHARED_SETTINGS_JSON = TEMPLATES_DIR / "settings.json"


# ---------------------------------------------------------------------------
# Adapter dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessAdapter:
    """Per-harness configuration. Pure data — no behavior.

    Attributes
    ----------
    id : str
        Stable adapter id used on the CLI (``--harness <id>``) and inside
        ``templates/<id>/``.
    install_root : str
        Project-relative directory the harness reads its config from
        (e.g. ``.claude``, ``.codex``, ``.pi``).
    agent_instructions_filename : str
        Top-level file the harness treats as the agent instructions file
        (``CLAUDE.md`` for Claude Code + Codex; ``AGENTS.md`` for OpenCode,
        pi, omp and omo). Mapped to ``marker = "<!-- BEGIN CLAUDE-PROTOCOL ORCHESTRATION -->"``
        by the orchestrator.
    settings_filename : str
        File name under ``install_root`` for the settings/config file.
        Format is JSON unless ``settings_filename`` ends in ``.yaml``
        (then expected content is the bundle's ``settings.yaml``).
    composes : tuple[str, ...]
        Other adapter ids this adapter re-exports. ``omo`` composes both
        ``opencode`` and ``codex``; resolving ``--harness omo`` emits both.
    uses_shared_rules : bool
        True for adapters that should also copy the shared ``templates/rules``
        contents (e.g. omp/pid). False for adapters that own the per-harness
        rules directory only.
    agent_instructions_template : str
        Path inside the per-harness dir to the agent instructions template.
    """

    id: str
    install_root: str
    agent_instructions_filename: str
    settings_filename: str = "settings.json"
    composes: tuple[str, ...] = ()
    uses_shared_rules: bool = False
    agent_instructions_template: str = "AGENTS.md"

    # ------------------------------------------------------------------ paths

    @property
    def adapter_dir(self) -> Path:
        """Directory holding harness-specific templates."""
        return TEMPLATES_DIR / self.id

    @property
    def settings_source(self) -> Path | None:
        """Per-harness settings.json template, or None."""
        candidate = self.adapter_dir / self.settings_filename
        if candidate.exists():
            return candidate
        if self.settings_filename == "settings.json" and SHARED_SETTINGS_JSON.exists():
            return SHARED_SETTINGS_JSON
        return None

    @property
    def agent_instructions_source(self) -> Path | None:
        """Per-harness agent-instructions template, or shared fallback."""
        custom = self.adapter_dir / self.agent_instructions_template
        if custom.exists():
            return custom
        if self.agent_instructions_filename == "CLAUDE.md" and SHARED_CLAUDE_MD.exists():
            return SHARED_CLAUDE_MD
        fallback = TEMPLATES_DIR / "AGENTS.md"
        return fallback if fallback.exists() else None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


CLAUDE = HarnessAdapter(
    id="claude",
    install_root=".claude",
    agent_instructions_filename="CLAUDE.md",
    settings_filename="settings.json",
    agent_instructions_template="CLAUDE.md",
)
CODEX = HarnessAdapter(
    id="codex",
    install_root=".codex",
    agent_instructions_filename="CLAUDE.md",
    settings_filename="settings.json",
    agent_instructions_template="CLAUDE.md",
)
OPENCODE = HarnessAdapter(
    id="opencode",
    install_root=".opencode",
    agent_instructions_filename="AGENTS.md",
    settings_filename="opencode.json",
)
PI = HarnessAdapter(
    id="pi",
    install_root=".pi",
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
)
OMP = HarnessAdapter(
    id="omp",
    install_root=".omp",
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
    uses_shared_rules=True,
)
OMO = HarnessAdapter(
    id="omo",
    install_root=".omo",
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
    composes=("opencode", "codex"),
    uses_shared_rules=True,
)


ALL_ADAPTERS: tuple[HarnessAdapter, ...] = (CLAUDE, CODEX, OPENCODE, PI, OMP, OMO)
ALL_IDS: frozenset[str] = frozenset(a.id for a in ALL_ADAPTERS)
DEFAULT_ADAPTER = CLAUDE  # v3.x backward compatibility


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def validate(value: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a known harness id or ``all``."""
    if value == "all":
        return
    if value not in ALL_IDS:
        raise ValueError(
            f"unknown --harness value: {value!r}. "
            f"Allowed: {sorted(ALL_IDS) + ['all']}"
        )


def resolve(value: str) -> list[HarnessAdapter]:
    """Resolve a ``--harness`` CLI flag to a list of adapters to install."""
    validate(value)
    if value == "all":
        seen: set[str] = set()
        ordered: list[HarnessAdapter] = []
        for a in ALL_ADAPTERS:
            for member in _expand(a):
                if member.id not in seen:
                    ordered.append(member)
                    seen.add(member.id)
        return ordered
    for a in ALL_ADAPTERS:
        if a.id == value:
            return _expand(a)


def _expand(adapter: HarnessAdapter) -> list[HarnessAdapter]:
    """Return ``[adapter, *adapter.composes_resolved]`` in declaration order."""
    out: list[HarnessAdapter] = [adapter]
    if not adapter.composes:
        return out
    by_id = {a.id: a for a in ALL_ADAPTERS}
    seen: set[str] = {adapter.id}
    for member_id in adapter.composes:
        member = by_id.get(member_id)
        if member is None or member.id in seen:
            continue
        out.append(member)
        seen.add(member.id)
    return out


__all__ = [
    "HarnessAdapter",
    "CLAUDE",
    "CODEX",
    "OPENCODE",
    "PI",
    "OMP",
    "OMO",
    "ALL_ADAPTERS",
    "ALL_IDS",
    "DEFAULT_ADAPTER",
    "TEMPLATES_DIR",
    "SHARED_AGENTS_DIR",
    "SHARED_HOOKS_DIR",
    "SHARED_RULES_DIR",
    "SHARED_SKILLS_DIR",
    "resolve",
    "validate",
]
