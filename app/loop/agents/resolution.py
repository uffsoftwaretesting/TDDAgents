"""
Per-agent tool resolution and filtering.

Implements I2 conforming to §4.5:
- Resolves agent declared tools against available system tools.
- Supports wildcard ('*') and explicit tool lists.
- Filters disallowed tools for subagents (preventing infinite recursion).
- Handles parameter specs like Agent(worker, researcher).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Sequence

from app.loop.agents.definition import AgentDefinition
from app.loop.tools.base import Tool

logger = logging.getLogger(__name__)

ALL_AGENT_DISALLOWED_TOOLS = frozenset({
    "AskUserQuestion",
    "ask_question",
    "ExitPlanMode",
    "EnterPlanMode",
    "TaskStop",
    "TaskOutput",
})

CUSTOM_AGENT_DISALLOWED_TOOLS = frozenset(ALL_AGENT_DISALLOWED_TOOLS)

ASYNC_AGENT_ALLOWED_TOOLS = frozenset({
    "ReadFile",
    "read_file",
    "Grep",
    "grep",
    "Glob",
    "glob",
    "WebSearch",
    "web_search",
    "WebFetch",
    "web_fetch",
})

_TOOL_SPEC_RE = re.compile(r"^([A-Za-z0-9_-]+)(?:[\(:]([^\)]*)[\)]?)?$")


@dataclass(frozen=True, slots=True)
class ResolvedAgentTools:
    """Outcome of resolving an agent's requested tools against available tools."""

    has_wildcard: bool
    valid_tools: tuple[str, ...]
    invalid_tools: tuple[str, ...]
    resolved_tools: tuple[Tool, ...]
    allowed_agent_types: tuple[str, ...] | None = None


def parse_tool_spec(spec: str) -> tuple[str, str | None]:
    """Parse a tool specification, e.g. 'Agent(worker, researcher)' -> ('Agent', 'worker, researcher')."""
    match = _TOOL_SPEC_RE.match(spec.strip())
    if match:
        name = match.group(1)
        args = match.group(2)
        return name, args.strip() if args else None
    return spec.strip(), None


def _tool_matches_name(tool: Tool, name: str) -> bool:
    """Check if a tool matches a given name (case-insensitive or matching aliases)."""
    norm_name = name.strip().lower()
    if tool.name.lower() == norm_name:
        return True
    return any(a.lower() == norm_name for a in getattr(tool, "aliases", ()))


def filter_tools_for_agent(
    tools: Sequence[Tool],
    is_async: bool = False,
    permission_mode: str | None = None,
    allow_nested_agent: bool = False,
) -> list[Tool]:
    """Filter candidate tools for a subagent before resolving allowlists."""
    filtered: list[Tool] = []
    for tool in tools:
        # MCP tools are always preserved
        if tool.name.startswith("mcp__"):
            filtered.append(tool)
            continue

        # In plan mode, ExitPlanMode is allowed
        if permission_mode == "plan" and tool.name == "ExitPlanMode":
            filtered.append(tool)
            continue

        # Check nested agent permission
        if tool.name in ("Agent", "Task"):
            if not allow_nested_agent:
                continue
        elif tool.name in ALL_AGENT_DISALLOWED_TOOLS:
            continue

        # Check async agent restrictions
        if is_async and tool.name not in ASYNC_AGENT_ALLOWED_TOOLS:
            continue

        filtered.append(tool)
    return filtered


def resolve_agent_tools(
    agent_definition: AgentDefinition,
    available_tools: Sequence[Tool],
    is_async: bool = False,
    is_main_thread: bool = False,
    allow_nested_agent: bool = False,
) -> ResolvedAgentTools:
    """
    Resolve and validate agent tools against available system tools.

    - Wildcard ('*' or None) resolves all filtered available tools.
    - Explicit list verifies each tool against available pool.
    - Disallowed tools list filters out forbidden tools.
    """
    candidate_tools = (
        list(available_tools)
        if is_main_thread
        else filter_tools_for_agent(
            available_tools,
            is_async=is_async,
            permission_mode=agent_definition.permission_mode,
            allow_nested_agent=allow_nested_agent,
        )
    )

    # Disallowed tools filter
    disallowed_set = {
        parse_tool_spec(s)[0].lower()
        for s in (agent_definition.disallowed_tools or ())
    }
    allowed_candidates = [
        t for t in candidate_tools
        if t.name.lower() not in disallowed_set
        and not any(a.lower() in disallowed_set for a in getattr(t, "aliases", ()))
    ]

    has_wildcard = (
        agent_definition.tools is None
        or (len(agent_definition.tools) == 1 and agent_definition.tools[0] == "*")
    )

    if has_wildcard:
        return ResolvedAgentTools(
            has_wildcard=True,
            valid_tools=(),
            invalid_tools=(),
            resolved_tools=tuple(allowed_candidates),
            allowed_agent_types=None,
        )

    valid_tools: list[str] = []
    invalid_tools: list[str] = []
    resolved: list[Tool] = []
    seen: set[str] = set()
    allowed_agent_types: list[str] | None = None

    for tool_spec in (agent_definition.tools or ()):
        base_name, args = parse_tool_spec(tool_spec)

        # Handle Agent metadata (allowedAgentTypes)
        if base_name in ("Agent", "Task") and args:
            allowed_agent_types = [t.strip() for t in args.split(",") if t.strip()]

        matched_tool: Tool | None = None
        for candidate in allowed_candidates:
            if _tool_matches_name(candidate, base_name):
                matched_tool = candidate
                break

        if matched_tool is not None:
            valid_tools.append(tool_spec)
            if matched_tool.name not in seen:
                seen.add(matched_tool.name)
                resolved.append(matched_tool)
        else:
            invalid_tools.append(tool_spec)
            logger.warning(
                "Agent '%s' requested unavailable tool '%s'",
                agent_definition.name,
                tool_spec,
            )

    return ResolvedAgentTools(
        has_wildcard=False,
        valid_tools=tuple(valid_tools),
        invalid_tools=tuple(invalid_tools),
        resolved_tools=tuple(resolved),
        allowed_agent_types=tuple(allowed_agent_types) if allowed_agent_types is not None else None,
    )
