"""
Agent definition loader and multi-tier override resolution.

Implements I1 conforming to §4.5:
- 4 parser discipline rules: omission unset, invalid logged and ignored,
  loud phase validation, inherit is sentinel.
- Multi-tier discovery: built-in, user (~/.tddagents/agents/), project (.tddagents/agents/).
- Precedence: project > user > built-in.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.context.instructions import strip_html_comments
from app.loop.ledger import TddPhase

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}|\$\{([A-Za-z0-9_]+)\}|\{([A-Za-z0-9_]+)\}")

_VALID_PERMISSION_MODES = frozenset({
    "default",
    "plan",
    "read_only",
    "workspace_write",
    "full",
    "bypass",
    "bypass_permissions",
    "accept_edits",
    "dont_ask",
})

_VALID_MEMORIES = frozenset({
    "run",
    "session",
    "local",
    "project",
    "user",
    "none",
})


def render_prompt(
    template: str,
    vars: Mapping[str, Any] | None = None,
    strip_comments: bool = True,
) -> str:
    """
    Render prompt text by substituting {{VAR}}, ${VAR}, or {VAR} placeholders.

    Load-bearing acceptance rule (§4.1):
    Unresolved placeholders survive visibly in the text as {{VAR}} or {VAR}.
    They NEVER render as an empty string.
    """
    text = template
    if strip_comments:
        text = strip_html_comments(text)

    if not vars:
        return text

    def _replace(match: Any) -> str:
        key = match.group(1) or match.group(2) or match.group(3)
        if key in vars:
            return str(vars[key])
        return str(match.group(0))

    return str(_VAR_RE.sub(_replace, text))


def parse_markdown_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter delimited by leading '---' or '<!--'.

    Returns (frontmatter_dict, markdown_body).
    """
    stripped = content.lstrip()
    if stripped.startswith("---"):
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1]
            body = parts[2]
            try:
                data = yaml.safe_load(raw_fm)
                fm = data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning("Failed to parse YAML frontmatter: %s", e)
                fm = {}
            return fm, body.strip()
    elif stripped.startswith("<!--"):
        parts = stripped.split("-->", 1)
        if len(parts) >= 2:
            raw_fm = parts[0][4:]
            body = parts[1]
            try:
                data = yaml.safe_load(raw_fm)
                fm = data if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning("Failed to parse HTML comment frontmatter: %s", e)
                fm = {}
            return fm, body.strip()

    return {}, content.strip()


def load_agent_definition(
    content: str,
    vars: Mapping[str, Any] | None = None,
    source: str = "built-in",
    base_dir: str = "",
) -> AgentDefinition:
    """
    Parse an AGENT.md content into a validated AgentDefinition.

    Follows the 4 discipline rules (§4.5):
    1. Omission means unset (None).
    2. Invalid value is logged and ignored (except phase).
    3. Enumerations validate against explicit list.
    4. inherit is a sentinel.
    """
    fm, raw_body = parse_markdown_frontmatter(content)
    prompt = render_prompt(raw_body, vars, strip_comments=True)

    name = str(fm.get("name") or "unnamed_agent")
    description = str(fm.get("description") or "")

    # Phase validation (The one field that fails loudly §4.5)
    phase: TddPhase | None = None
    if "phase" in fm:
        raw_phase = fm["phase"]
        if raw_phase is not None:
            norm_phase = str(raw_phase).strip().upper()
            if norm_phase == "POST_GREEN":
                norm_phase = "REFACTOR"
            try:
                phase = TddPhase(norm_phase)
            except ValueError:
                allowed = [p.value for p in TddPhase] + ["post_green"]
                raise AgentFrontmatterError(
                    f"Invalid phase '{raw_phase}' in agent '{name}'. Allowed phases: {allowed}"
                ) from None

    # Tools validation
    tools: tuple[str, ...] | None = None
    if "tools" in fm:
        raw_tools = fm["tools"]
        if isinstance(raw_tools, (list, tuple)) and all(isinstance(t, str) for t in raw_tools):
            tools = tuple(raw_tools)
        else:
            logger.warning("Invalid tools list in agent '%s': %r", name, raw_tools)

    # Disallowed tools validation
    disallowed_tools: tuple[str, ...] | None = None
    raw_disallowed = fm.get("disallowedTools", fm.get("disallowed_tools"))
    if raw_disallowed is not None:
        if isinstance(raw_disallowed, (list, tuple)) and all(isinstance(t, str) for t in raw_disallowed):
            disallowed_tools = tuple(raw_disallowed)
        else:
            logger.warning("Invalid disallowed_tools in agent '%s': %r", name, raw_disallowed)

    # PermissionMode validation
    permission_mode: str | None = None
    if "permissionMode" in fm:
        raw_pm = fm["permissionMode"]
        if isinstance(raw_pm, str) and raw_pm.lower() in _VALID_PERMISSION_MODES:
            permission_mode = raw_pm.lower()
        else:
            logger.warning("Invalid permissionMode in agent '%s': %r", name, raw_pm)

    # Memory validation
    memory: str | None = None
    if "memory" in fm:
        raw_mem = fm["memory"]
        if isinstance(raw_mem, str) and raw_mem.lower() in _VALID_MEMORIES:
            memory = raw_mem.lower()
        else:
            logger.warning("Invalid memory in agent '%s': %r", name, raw_mem)

    # Model validation
    model: str | None = None
    if "model" in fm:
        raw_model = fm["model"]
        if isinstance(raw_model, str):
            model = raw_model
        else:
            logger.warning("Invalid model in agent '%s': %r", name, raw_model)

    # ForkFrom validation
    fork_from: str | None = None
    if "forkFrom" in fm:
        raw_fork = fm["forkFrom"]
        if isinstance(raw_fork, str):
            fork_from = raw_fork
        else:
            logger.warning("Invalid forkFrom in agent '%s': %r", name, raw_fork)

    # RevertOnRed validation
    revert_on_red: bool | None = None
    if "revertOnRed" in fm:
        raw_ror = fm["revertOnRed"]
        if isinstance(raw_ror, bool):
            revert_on_red = raw_ror
        else:
            logger.warning("Invalid revertOnRed in agent '%s': %r", name, raw_ror)

    # Background validation
    background: bool | None = None
    if "background" in fm:
        raw_bg = fm["background"]
        if isinstance(raw_bg, bool):
            background = raw_bg
        else:
            logger.warning("Invalid background in agent '%s': %r", name, raw_bg)

    # Hooks validation
    hooks: dict[str, Any] | None = None
    if "hooks" in fm:
        raw_hooks = fm["hooks"]
        if isinstance(raw_hooks, dict):
            hooks = raw_hooks
        else:
            logger.warning("Invalid hooks dict in agent '%s': %r", name, raw_hooks)

    return AgentDefinition(
        name=name,
        description=description,
        prompt=prompt,
        phase=phase,
        tools=tools,
        disallowed_tools=disallowed_tools,
        permission_mode=permission_mode,
        memory=memory,
        fork_from=fork_from,
        revert_on_red=revert_on_red,
        hooks=hooks,
        model=model,
        background=background,
        source=source,
        base_dir=base_dir,
        raw_frontmatter=dict(fm),
    )


def load_agent_definition_from_path(
    path: Path | str,
    vars: Mapping[str, Any] | None = None,
    source: str = "built-in",
) -> AgentDefinition:
    """Load and parse an AGENT.md file from disk."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Agent definition file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    return load_agent_definition(content, vars, source=source, base_dir=str(file_path.parent))


def _scan_agent_dir(
    directory: Path,
    source: str,
    vars: Mapping[str, Any] | None = None,
) -> dict[str, AgentDefinition]:
    """Scan a directory for AGENT.md files or .md files."""
    agents: dict[str, AgentDefinition] = {}
    if not directory.is_dir():
        return agents

    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            agent_md = entry / "AGENT.md"
            if agent_md.is_file():
                try:
                    agent_def = load_agent_definition_from_path(agent_md, vars=vars, source=source)
                    agents[agent_def.name] = agent_def
                except AgentFrontmatterError:
                    raise
                except Exception as e:
                    logger.warning("Failed loading agent from %s: %s", agent_md, e)
        elif entry.is_file() and entry.suffix == ".md" and entry.name != "README.md":
            try:
                agent_def = load_agent_definition_from_path(entry, vars=vars, source=source)
                agents[agent_def.name] = agent_def
            except AgentFrontmatterError:
                raise
            except Exception as e:
                logger.warning("Failed loading agent from %s: %s", entry, e)

    return agents


def get_agent_definitions_with_overrides(
    project_dir: Path | str | None = None,
    user_home: Path | str | None = None,
    built_in_dir: Path | str | None = None,
    vars: Mapping[str, Any] | None = None,
) -> dict[str, AgentDefinition]:
    from app.loop.prompts.registry import global_prompt_registry
    
    definitions: dict[str, AgentDefinition] = {}

    # 1. Built-in
    if built_in_dir is not None:
        built_in_path = Path(built_in_dir)
        if built_in_path.is_dir():
            definitions.update(_scan_agent_dir(built_in_path, source="built-in", vars=vars))
    else:
        built_in_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "agents"
        if built_in_path.is_dir():
            definitions.update(_scan_agent_dir(built_in_path, source="built-in", vars=vars))

    # 2. User
    if user_home is not None:

        user_path = Path(user_home) / ".tddagents" / "agents"
    else:
        user_path = Path.home() / ".tddagents" / "agents"

    if user_path.is_dir():
        definitions.update(_scan_agent_dir(user_path, source="user", vars=vars))

    # 3. Project
    if project_dir is not None:
        proj_path = Path(project_dir) / ".tddagents" / "agents"
    else:
        proj_path = Path.cwd() / ".tddagents" / "agents"

    if proj_path.is_dir():
        definitions.update(_scan_agent_dir(proj_path, source="project", vars=vars))

    return definitions
