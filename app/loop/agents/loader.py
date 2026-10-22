"""
Agent definitions from Markdown (`reference/claude-code/src/tools/AgentTool/loadAgentsDir.ts`).

* `load_agent_definition` is `parseAgentFromMarkdown`: `name` and `description` are required
  (without them the file is not an agent), every other invalid value is logged and ignored,
  and only upstream's `PERMISSION_MODES` parse. `phase` is the one TDDAgents field that fails
  the load loudly.
* `get_agent_definitions_with_overrides` is `getAgentDefinitionsWithOverrides` +
  `getActiveAgentsFromList`: the built-in roster in `app/prompts/agents/`, then user
  (`~/.tddagents/agents/`), then project (`.tddagents/agents/`), a later source replacing an
  earlier agent of the same name. No prompt registry is consulted.

Not ported (no subsystem here): `color`, `effort`, `isolation`, `mcpServers`, `skills`,
`initialPrompt`, and `omitClaudeMd` on file agents (upstream sets it on built-ins only).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.context.instructions import strip_html_comments
from app.loop.context.memory import is_auto_memory_enabled
from app.loop.ledger import TddPhase
from app.loop.permissions.settings import parse_tool_list_from_cli
from app.loop.permissions.types import PERMISSION_MODES

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}|\$\{([A-Za-z0-9_]+)\}|\{([A-Za-z0-9_]+)\}")

#: `parseInt(String(value), 10)`: the leading integer of a string.
_LEADING_INT_RE = re.compile(r"^\s*[+-]?\d+")

#: Upstream's `PERMISSION_MODES` (with `auto`, which the classifier build adds).
_VALID_PERMISSION_MODES: tuple[str, ...] = tuple(str(mode) for mode in PERMISSION_MODES)

#: Upstream's `user`/`project`/`local`, plus TDDAgents' run-scoped memory (Part I5).
_VALID_MEMORIES = frozenset({"user", "project", "local", "run", "session", "none"})

#: `FILE_WRITE_TOOL_NAME`, `FILE_EDIT_TOOL_NAME`, `FILE_READ_TOOL_NAME`, in upstream's order.
_MEMORY_FILE_TOOLS = ("WriteFile", "Edit", "ReadFile")


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
            except yaml.YAMLError as e:
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
            except yaml.YAMLError as e:
                logger.warning("Failed to parse HTML comment frontmatter: %s", e)
                fm = {}
            return fm, body.strip()

    return {}, content.strip()


def _parse_positive_int(value: Any) -> int | None:
    """`parsePositiveIntFromFrontmatter`."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed: int | None = value
    elif isinstance(value, float):
        parsed = int(value) if value.is_integer() else None
    else:
        match = _LEADING_INT_RE.match(str(value))
        parsed = int(match.group(0)) if match else None
    return parsed if parsed is not None and parsed > 0 else None


def _parse_tool_list(value: Any) -> list[str] | None:
    """`parseToolListString`: `None` when missing, `[]` for an empty value."""
    if value is None:
        return None
    if not value:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [item for item in value if isinstance(item, str)]
    else:
        items = []
    if not items:
        return []
    parsed = parse_tool_list_from_cli(items)
    return ["*"] if "*" in parsed else parsed


def parse_agent_tools_from_frontmatter(value: Any) -> tuple[str, ...] | None:
    """`parseAgentToolsFromFrontmatter`: missing or `*` = every tool (`None`), empty = none."""
    parsed = _parse_tool_list(value)
    if parsed is None:
        return None
    if "*" in parsed:
        return None
    return tuple(parsed)


def load_agent_definition(
    content: str,
    vars: Mapping[str, Any] | None = None,
    source: str = "built-in",
    base_dir: str = "",
) -> AgentDefinition | None:
    """
    `parseAgentFromMarkdown`: the definition, or `None` when the file is not an agent.

    A missing `name` or `description` is not an agent (upstream returns `null`); every other
    invalid value is logged and ignored. The one exception is `phase`, which raises
    `AgentFrontmatterError`, because dropping it would silently disable the TDD invariant.
    """
    fm, raw_body = parse_markdown_frontmatter(content)
    name = fm.get("name")
    if not name or not isinstance(name, str):
        return None
    description = fm.get("description")
    if not description or not isinstance(description, str):
        logger.debug("Agent file %s is missing required 'description' in frontmatter", base_dir or name)
        return None
    description = description.replace("\\n", "\n")

    phase: TddPhase | None = None
    raw_phase = fm.get("phase")
    if raw_phase is not None:
        norm_phase = str(raw_phase).strip().upper()
        if norm_phase == "POST_GREEN":  # the plan's name for REFACTOR (§4.5)
            norm_phase = "REFACTOR"
        try:
            phase = TddPhase(norm_phase)
        except ValueError:
            allowed = [p.value for p in TddPhase] + ["post_green"]
            raise AgentFrontmatterError(
                f"Invalid phase '{raw_phase}' in agent '{name}'. Allowed phases: {allowed}"
            ) from None

    model: str | None = None
    raw_model = fm.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        trimmed = raw_model.strip()
        model = "inherit" if trimmed.lower() == "inherit" else trimmed

    raw_background = fm.get("background")
    if raw_background not in (None, "true", "false", True, False):
        logger.debug(
            "Agent %s has invalid background value '%s'. Must be 'true', 'false', or omitted.", name, raw_background
        )
    background = True if raw_background in ("true", True) else None

    memory: str | None = None
    raw_memory = fm.get("memory")
    if raw_memory is not None:
        if raw_memory in _VALID_MEMORIES:
            memory = raw_memory
        else:
            logger.debug("Agent %s has invalid memory value '%s'.", name, raw_memory)

    permission_mode: str | None = None
    raw_mode = fm.get("permissionMode")
    if raw_mode:
        if raw_mode in _VALID_PERMISSION_MODES:
            permission_mode = raw_mode
        else:
            logger.debug(
                "Agent %s has invalid permissionMode '%s'. Valid options: %s",
                name, raw_mode, ", ".join(_VALID_PERMISSION_MODES),
            )

    raw_max_turns = fm.get("maxTurns")
    max_turns = _parse_positive_int(raw_max_turns)
    if raw_max_turns is not None and max_turns is None:
        logger.debug("Agent %s has invalid maxTurns '%s'. Must be a positive integer.", name, raw_max_turns)

    tools = parse_agent_tools_from_frontmatter(fm.get("tools"))
    # `isAutoMemoryEnabled() && memory && tools !== undefined`: memory needs the file tools.
    if is_auto_memory_enabled() and memory and tools is not None:
        for tool in _MEMORY_FILE_TOOLS:
            if tool not in tools:
                tools = (*tools, tool)

    disallowed_tools = (
        parse_agent_tools_from_frontmatter(fm["disallowedTools"]) if "disallowedTools" in fm else None
    )

    hooks: dict[str, Any] | None = None
    raw_hooks = fm.get("hooks")
    if raw_hooks is not None:
        if isinstance(raw_hooks, dict):
            hooks = raw_hooks
        else:
            logger.debug("Agent %s has invalid hooks: %r", name, raw_hooks)

    return AgentDefinition(
        name=name,
        description=description,
        prompt=render_prompt(raw_body, vars, strip_comments=True),
        phase=phase,
        tools=tools,
        disallowed_tools=disallowed_tools,
        permission_mode=permission_mode,
        memory=memory,
        max_turns=max_turns,
        hooks=hooks,
        model=model,
        background=background,
        omit_claude_md=source == "built-in" and fm.get("omitClaudeMd") is True,
        source=source,
        base_dir=base_dir,
        raw_frontmatter=dict(fm),
    )


def load_agent_definition_from_path(
    path: Path | str,
    vars: Mapping[str, Any] | None = None,
    source: str = "built-in",
) -> AgentDefinition | None:
    """Load and parse an agent Markdown file; `None` when it is not an agent."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Agent definition file not found: {file_path}")
    content = file_path.read_text(encoding="utf-8")
    return load_agent_definition(content, vars, source=source, base_dir=str(file_path.parent))


def _load_if_agent(path: Path, source: str, vars: Mapping[str, Any] | None) -> AgentDefinition | None:
    try:
        return load_agent_definition_from_path(path, vars=vars, source=source)
    except AgentFrontmatterError:
        raise
    except Exception as e:  # noqa: BLE001 - upstream logs a failed agent file and skips it
        logger.warning("Failed loading agent from %s: %s", path, e)
        return None


def _scan_agent_dir(
    directory: Path,
    source: str,
    vars: Mapping[str, Any] | None = None,
) -> dict[str, AgentDefinition]:
    """Every agent in a directory: `<dir>/AGENT.md` entries and flat `.md` files."""
    agents: dict[str, AgentDefinition] = {}
    if not directory.is_dir():
        return agents
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            candidate = entry / "AGENT.md"
            if not candidate.is_file():
                continue
        elif entry.is_file() and entry.suffix == ".md" and entry.name != "README.md":
            candidate = entry
        else:
            continue
        agent_def = _load_if_agent(candidate, source, vars)
        if agent_def is not None:
            agents[agent_def.name] = agent_def
    return agents


#: The built-in roster: `app/prompts/agents/`.
BUILT_IN_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "agents"


def get_agent_definitions_with_overrides(
    project_dir: Path | str | None = None,
    user_home: Path | str | None = None,
    built_in_dir: Path | str | None = None,
    vars: Mapping[str, Any] | None = None,
) -> dict[str, AgentDefinition]:
    """`getAgentDefinitionsWithOverrides`: the active agents, built-in < user < project."""
    definitions: dict[str, AgentDefinition] = {}
    definitions.update(_scan_agent_dir(Path(built_in_dir or BUILT_IN_AGENTS_DIR), "built-in", vars))
    home = Path(user_home) if user_home is not None else Path.home()
    definitions.update(_scan_agent_dir(home / ".tddagents" / "agents", "user", vars))
    project = Path(project_dir) if project_dir is not None else Path.cwd()
    definitions.update(_scan_agent_dir(project / ".tddagents" / "agents", "project", vars))
    return definitions
