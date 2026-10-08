"""
Markdown prompt and agent definition loader.

Replaces Jinja2 with plain Markdown, explicit variable substitution,
and disciplined frontmatter parsing as specified in §4 and §5 Part E4.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.loop.agents.definition import AgentDefinition, AgentFrontmatterError
from app.loop.context.instructions import strip_html_comments
from app.loop.ledger import TddPhase

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}|\$\{([A-Za-z0-9_]+)\}|\{([A-Za-z0-9_]+)\}")

_VALID_PERMISSION_MODES = frozenset({"read_only", "workspace_write", "full", "bypass"})
_VALID_MEMORIES = frozenset({"run", "session", "none"})

__all__ = [
    "AgentDefinition",
    "AgentFrontmatterError",
    "load_agent_definition",
    "load_agent_definition_from_path",
    "parse_markdown_frontmatter",
    "render_prompt",
]


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

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or match.group(3)
        if key in vars:
            return str(vars[key])
        return match.group(0)

    return _VAR_RE.sub(_replace, text)


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
        permission_mode=permission_mode,
        memory=memory,
        fork_from=fork_from,
        revert_on_red=revert_on_red,
        hooks=hooks,
        model=model,
        raw_frontmatter=dict(fm),
    )


def load_agent_definition_from_path(
    path: Path | str,
    vars: Mapping[str, Any] | None = None,
) -> AgentDefinition:
    """Load and parse an AGENT.md file from disk."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Agent definition file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    return load_agent_definition(content, vars)
