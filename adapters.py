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
    settings_in_install_root : bool
        If True (default), the settings file lives at ``<install_root>/<settings_filename>``
        (e.g. ``.codex/config.toml``, ``.claude/settings.json``).
        If False, the settings file lives at the project root with
        ``settings_filename`` as the leaf (e.g. ``./opencode.json`` for
        OpenCode 1.18+, where ``.opencode/`` is the capability root for
        plugins/agents/skills but the runtime config is a root-level file).
    project_instructions_at_root : bool
        If True, agent instructions are written to the project root
        (``./AGENTS.md``) instead of inside ``install_root``
        (``./.opencode/AGENTS.md``). OpenCode 1.18+ reads the project-wide
        ``AGENTS.md`` from the working directory; ``.opencode/AGENTS.md``
        is silently ignored. Codex reads only the root- or
        ancestor-``AGENTS.md``, never a harness-internal copy.
    """

    id: str
    install_root: str
    agent_instructions_filename: str
    settings_filename: str = "settings.json"
    composes: tuple[str, ...] = ()
    uses_shared_rules: bool = False
    agent_instructions_template: str = "AGENTS.md"
    settings_in_install_root: bool = True
    project_instructions_at_root: bool = False

    # ------------------------------------------------------------------ paths

    @property
    def adapter_dir(self) -> Path:
        """Directory holding harness-specific templates."""
        return TEMPLATES_DIR / self.id

    @property
    def settings_source(self) -> Path | None:
        """Per-harness settings template, or shared fallback.

        For OpenCode (settings_in_install_root=False) the file lives at the
        project root (./opencode.json) rather than .opencode/opencode.json.
        The OpenCode runtime reads only the root-level config; writing into
        .opencode/ would put it where no loader looks.
        """
        candidate = self.adapter_dir / self.settings_filename
        if candidate.exists():
            return candidate
        if self.settings_filename == "settings.json" and SHARED_SETTINGS_JSON.exists():
            return SHARED_SETTINGS_JSON
        return None

    def settings_destination(self, project_dir: Path) -> Path:
        """Project-relative destination for the settings file.

        Respects ``settings_in_install_root`` (False → project root,
        True → inside ``install_root``).
        """
        if self.settings_in_install_root:
            return project_dir / self.install_root / self.settings_filename
        return project_dir / self.settings_filename

    def settings_rel_key(self) -> str:
        """Recorder key for the settings file (matches destination layout)."""
        if self.settings_in_install_root:
            return f"{self.install_root}/{self.settings_filename}"
        return self.settings_filename

    def agent_instructions_destination(self, project_dir: Path) -> Path:
        """Project-relative destination for the agent instructions file.

        Respects ``project_instructions_at_root`` (True → project root,
        False → inside ``install_root``). OpenCode and Codex read root-level
        AGENTS.md; writing into .opencode/AGENTS.md is silently ignored.
        """
        if self.project_instructions_at_root:
            return project_dir / self.agent_instructions_filename
        return project_dir / self.install_root / self.agent_instructions_filename

    def agent_instructions_rel_key(self) -> str:
        """Recorder key for the agent instructions file."""
        if self.project_instructions_at_root:
            return self.agent_instructions_filename
        return f"{self.install_root}/{self.agent_instructions_filename}"

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
    # Codex CLI reads the global ~/.codex/AGENTS.md + a project- or
    # ancestor-level AGENTS.md; it does NOT read .codex/CLAUDE.md and
    # has no documented .codex/settings.json schema. AGENTS.md stays
    # at the project root via project_instructions_at_root=True so the
    # Codex discovery path actually finds it.
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.toml",
    agent_instructions_template="AGENTS.md",
    project_instructions_at_root=True,
    uses_shared_rules=True,  # .codex/rules/ is read natively
)
OPENCODE = HarnessAdapter(
    id="opencode",
    install_root=".opencode",
    # OpenCode 1.18+ reads runtime config from ./opencode.json (project
    # root), NOT .opencode/opencode.json — .opencode/ is the capability
    # root for plugins/agents/skills/commands only. Agent instructions
    # are read from the project-root or ancestor AGENTS.md via
    # agent-discovery; .opencode/AGENTS.md is silently ignored.
    agent_instructions_filename="AGENTS.md",
    settings_filename="opencode.json",
    settings_in_install_root=False,
    project_instructions_at_root=True,
)
PI = HarnessAdapter(
    id="pi",
    install_root=".pi",
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
    project_instructions_at_root=True,
)
OMP = HarnessAdapter(
    id="omp",
    install_root=".omp",
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
    uses_shared_rules=True,
    project_instructions_at_root=True,
)
OMO = HarnessAdapter(
    id="omo",
    install_root=".omo",
    # OMO is a composition alias: it materializes the opencode + codex
    # capability trees. The .omo/ dir is only kept for the OMO-specific
    # settings overlay; instructions live at the project root because
    # the composed harnesses (opencode, codex) both read root AGENTS.md.
    agent_instructions_filename="AGENTS.md",
    settings_filename="config.yml",
    composes=("opencode", "codex"),
    project_instructions_at_root=True,
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
