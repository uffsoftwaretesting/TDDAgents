"""
The `agent_listing_delta` and `mcp_instructions_delta` sources.

Ported from `reference/claude-code/src/utils/attachments.ts` ->
`getAgentListingDeltaAttachment`, `getMcpInstructionsDeltaAttachment`;
`src/utils/mcpInstructionsDelta.ts` -> `getMcpInstructionsDelta`,
`isMcpInstructionsDeltaEnabled`; `src/tools/AgentTool/prompt.ts` -> `formatAgentLine`,
`getToolsDescription`, `shouldInjectAgentListInMessages`.

Both diff what the conversation already announced (by scanning its attachments) against what
is available now, and say only what changed. **Both are off unless their env gate is set**, as
upstream's external build has them (`tengu_agent_list_attach` / `tengu_basalt_3kr` default
false): `TDDAGENTS_AGENT_LIST_IN_MESSAGES`, `TDDAGENTS_MCP_INSTR_DELTA` (upstream's
`CLAUDE_CODE_*`, renamed). The agent roster and MCP clients are read from the context
(`agent_definitions`, `mcp_clients`); a context without them announces nothing.

Not ported: the MCP-requirement and permission-deny filters on the roster (the Agent tool that
enforces them is Phase 7), and the client-side Chrome instruction (no such client).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from app.loop.attachments.types import Attachment, attachment_of
from app.loop.messages import Message

AGENT_TOOL_NAME = "Agent"
AGENT_LIST_ENV = "TDDAGENTS_AGENT_LIST_IN_MESSAGES"
MCP_INSTR_DELTA_ENV = "TDDAGENTS_MCP_INSTR_DELTA"

_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def get_tools_description(agent: Any) -> str:
    """`getToolsDescription`."""
    tools = list(getattr(agent, "tools", None) or ())
    denied = list(getattr(agent, "disallowed_tools", None) or ())
    if tools and denied:
        effective = [t for t in tools if t not in set(denied)]
        return ", ".join(effective) if effective else "None"
    if tools:
        return ", ".join(tools)
    if denied:
        return "All tools except " + ", ".join(denied)
    return "All tools"


def format_agent_line(agent: Any) -> str:
    """`formatAgentLine` (the definition's `description` is upstream's `whenToUse`)."""
    return f"- {agent.name}: {agent.description} (Tools: {get_tools_description(agent)})"


def agent_definitions_for(context: Any) -> Sequence[Any]:
    return tuple(getattr(context, "agent_definitions", ()) or ())


def mcp_clients_for(context: Any) -> Sequence[Any]:
    return tuple(getattr(context, "mcp_clients", ()) or ())


def _has_agent_tool(context: Any) -> bool:
    return any(
        getattr(t, "name", None) == AGENT_TOOL_NAME or AGENT_TOOL_NAME in getattr(t, "aliases", ())
        for t in getattr(context, "tools", ()) or ()
    )


def get_agent_listing_delta_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getAgentListingDeltaAttachment`."""
    if not _env_truthy(AGENT_LIST_ENV) or not _has_agent_tool(context):
        return []
    agents = list(agent_definitions_for(context))
    announced: set[str] = set()
    for message in messages:
        attachment = attachment_of(message)
        if not attachment or attachment.get("type") != "agent_listing_delta":
            continue
        announced.update(attachment.get("addedTypes") or ())
        announced.difference_update(attachment.get("removedTypes") or ())
    current = {a.name for a in agents}
    added = sorted((a for a in agents if a.name not in announced), key=lambda a: a.name)
    removed = sorted(t for t in announced if t not in current)
    if not added and not removed:
        return []
    return [Attachment("agent_listing_delta", {
        "addedTypes": [a.name for a in added],
        "addedLines": [format_agent_line(a) for a in added],
        "removedTypes": removed,
        "isInitial": len(announced) == 0,
        "showConcurrencyNote": True,
    })]


def _field(client: Any, name: str) -> Any:
    return client.get(name) if isinstance(client, Mapping) else getattr(client, name, None)


def get_mcp_instructions_delta_attachment(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getMcpInstructionsDeltaAttachment` / `getMcpInstructionsDelta`."""
    if not _env_truthy(MCP_INSTR_DELTA_ENV):
        return []
    announced: set[str] = set()
    for message in messages:
        attachment = attachment_of(message)
        if not attachment or attachment.get("type") != "mcp_instructions_delta":
            continue
        announced.update(attachment.get("addedNames") or ())
        announced.difference_update(attachment.get("removedNames") or ())
    connected = [c for c in mcp_clients_for(context) if _field(c, "type") == "connected"]
    connected_names = {str(_field(c, "name")) for c in connected}
    blocks = {
        str(_field(c, "name")): f"## {_field(c, 'name')}\n{_field(c, 'instructions')}"
        for c in connected
        if _field(c, "instructions")
    }
    added = sorted((name, block) for name, block in blocks.items() if name not in announced)
    removed = sorted(name for name in announced if name not in connected_names)
    if not added and not removed:
        return []
    return [Attachment("mcp_instructions_delta", {
        "addedNames": [name for name, _ in added],
        "addedBlocks": [block for _, block in added],
        "removedNames": removed,
    })]


__all__ = [
    "AGENT_LIST_ENV",
    "MCP_INSTR_DELTA_ENV",
    "agent_definitions_for",
    "format_agent_line",
    "get_agent_listing_delta_attachment",
    "get_mcp_instructions_delta_attachment",
    "get_tools_description",
    "mcp_clients_for",
]
