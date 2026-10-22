"""
Which tools a worker gets, and which agents a caller may spawn.

Ported from `reference/claude-code/src/tools/AgentTool/agentToolUtils.ts` ->
`filterToolsForAgent`, `resolveAgentTools`; `src/constants/tools.ts` ->
`ALL_AGENT_DISALLOWED_TOOLS`, `CUSTOM_AGENT_DISALLOWED_TOOLS`, `ASYNC_AGENT_ALLOWED_TOOLS`;
and `src/utils/permissions/permissions.ts` -> `filterDeniedAgents`, `getDenyRuleForAgent`.

The sets are the **external build's**, mapped onto this repo's tool names. `Agent` is in
`ALL_AGENT_DISALLOWED_TOOLS` (upstream allows nested agents for ants only), so no worker can
spawn a worker. Names match exactly, as upstream's `Set.has(tool.name)` and
`availableToolMap.get(toolName)` do; a spec is parsed with `permissionRuleValueFromString`,
which maps the legacy `Task` to `Agent`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from app.loop.agents.definition import AgentDefinition
from app.loop.permissions.settings import permission_rule_value_from_string
from app.loop.permissions.types import PermissionRule, ToolPermissionContext
from app.loop.tools.base import Tool

logger = logging.getLogger(__name__)

AGENT_TOOL_NAME = "Agent"

#: `EXIT_PLAN_MODE_V2_TOOL_NAME`.
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

#: `ALL_AGENT_DISALLOWED_TOOLS` (external build): `TaskOutput`, `ExitPlanMode`,
#: `EnterPlanMode`, `Agent`, `AskUserQuestion`, `TaskStop`.
ALL_AGENT_DISALLOWED_TOOLS = frozenset({
    "TaskOutput",
    EXIT_PLAN_MODE_TOOL_NAME,
    "EnterPlanMode",
    AGENT_TOOL_NAME,
    "AskUserQuestion",
    "TaskStop",
})

#: `CUSTOM_AGENT_DISALLOWED_TOOLS` is the same set upstream.
CUSTOM_AGENT_DISALLOWED_TOOLS = frozenset(ALL_AGENT_DISALLOWED_TOOLS)

#: `ASYNC_AGENT_ALLOWED_TOOLS`, in this repo's names: read, todo, search, shell, edit and
#: write. `RunTests` is TDDAgents' own test runner and joins them: it is the only writer of
#: the phase ledger, so a background worker without it could never produce TDD evidence.
ASYNC_AGENT_ALLOWED_TOOLS = frozenset({
    "ReadFile",
    "WebSearch",
    "TodoWrite",
    "Grep",
    "WebFetch",
    "Glob",
    "Bash",
    "Edit",
    "WriteFile",
    "Skill",
    "StructuredOutput",
    "ToolSearch",
    "RunTests",
})


@dataclass(frozen=True, slots=True)
class ResolvedAgentTools:
    """`ResolvedAgentTools`."""

    has_wildcard: bool
    valid_tools: tuple[str, ...]
    invalid_tools: tuple[str, ...]
    resolved_tools: tuple[Tool, ...]
    allowed_agent_types: tuple[str, ...] | None = None


def parse_tool_spec(spec: str) -> tuple[str, str | None]:
    """A tool spec as a permission rule value: `Agent(tester, developer)` -> name and content."""
    return permission_rule_value_from_string(spec)


def filter_tools_for_agent(
    tools: Sequence[Tool],
    *,
    is_built_in: bool,
    is_async: bool = False,
    permission_mode: str | None = None,
) -> list[Tool]:
    """`filterToolsForAgent`."""
    kept: list[Tool] = []
    for tool in tools:
        if tool.name.startswith("mcp__"):
            kept.append(tool)
            continue
        if tool.name == EXIT_PLAN_MODE_TOOL_NAME and permission_mode == "plan":
            kept.append(tool)
            continue
        if tool.name in ALL_AGENT_DISALLOWED_TOOLS:
            continue
        if not is_built_in and tool.name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue
        if is_async and tool.name not in ASYNC_AGENT_ALLOWED_TOOLS:
            continue
        kept.append(tool)
    return kept


def resolve_agent_tools(
    agent_definition: AgentDefinition,
    available_tools: Sequence[Tool],
    is_async: bool = False,
    is_main_thread: bool = False,
) -> ResolvedAgentTools:
    """`resolveAgentTools`: wildcard expansion and validation against the available pool."""
    filtered = (
        list(available_tools)
        if is_main_thread
        else filter_tools_for_agent(
            available_tools,
            is_built_in=agent_definition.source == "built-in",
            is_async=is_async,
            permission_mode=agent_definition.permission_mode,
        )
    )
    disallowed = {parse_tool_spec(spec)[0] for spec in agent_definition.disallowed_tools or ()}
    allowed = [tool for tool in filtered if tool.name not in disallowed]

    agent_tools = agent_definition.tools
    if agent_tools is None or tuple(agent_tools) == ("*",):
        return ResolvedAgentTools(has_wildcard=True, valid_tools=(), invalid_tools=(), resolved_tools=tuple(allowed))

    by_name = {tool.name: tool for tool in allowed}
    valid: list[str] = []
    invalid: list[str] = []
    resolved: list[Tool] = []
    allowed_agent_types: tuple[str, ...] | None = None
    for spec in agent_tools:
        tool_name, rule_content = parse_tool_spec(spec)
        if tool_name == AGENT_TOOL_NAME:
            if rule_content:
                allowed_agent_types = tuple(s.strip() for s in rule_content.split(","))
            if not is_main_thread:
                # Agent is filtered out for workers; the spec only carries the allowed types.
                valid.append(spec)
                continue
        tool = by_name.get(tool_name)
        if tool is None:
            invalid.append(spec)
            continue
        valid.append(spec)
        if tool not in resolved:
            resolved.append(tool)
    return ResolvedAgentTools(
        has_wildcard=False,
        valid_tools=tuple(valid),
        invalid_tools=tuple(invalid),
        resolved_tools=tuple(resolved),
        allowed_agent_types=allowed_agent_types,
    )


def _agent_deny_rules(context: ToolPermissionContext, agent_tool_name: str) -> Iterable[PermissionRule]:
    return (rule for rule in context.always_deny_rules if rule.tool_name == agent_tool_name)


def get_deny_rule_for_agent(
    context: ToolPermissionContext, agent_tool_name: str, agent_type: str
) -> PermissionRule | None:
    """`getDenyRuleForAgent`: the `Agent(<type>)` deny rule naming this agent type."""
    return next((r for r in _agent_deny_rules(context, agent_tool_name) if r.rule_content == agent_type), None)


_AgentT = TypeVar("_AgentT", bound=AgentDefinition)


def filter_denied_agents(
    agents: Sequence[_AgentT], context: ToolPermissionContext, agent_tool_name: str
) -> list[_AgentT]:
    """`filterDeniedAgents`: drop every agent type an `Agent(<type>)` deny rule names."""
    denied = {r.rule_content for r in _agent_deny_rules(context, agent_tool_name) if r.rule_content is not None}
    return [agent for agent in agents if agent.name not in denied]


__all__ = [
    "AGENT_TOOL_NAME",
    "ALL_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "EXIT_PLAN_MODE_TOOL_NAME",
    "ResolvedAgentTools",
    "filter_denied_agents",
    "filter_tools_for_agent",
    "get_deny_rule_for_agent",
    "parse_tool_spec",
    "resolve_agent_tools",
]
