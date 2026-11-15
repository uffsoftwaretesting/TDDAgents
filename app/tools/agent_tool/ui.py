"""
What a running agent is doing, in one line.

Ported from `reference/claude-code/src/tools/AgentTool/UI.tsx` -> `extractLastToolInfo` and its
`getSearchOrReadInfo`: two or more trailing searches/reads roll up into an active summary
("Searching for 2 patterns, reading 1 file…"), else the last finished tool reads as
`<user-facing name>: <tool-use summary>` ("Search: def main"), its name alone, or the raw tool name
when the tool is unknown. The progress it reads is the worker's messages, one per content block,
as the Agent tool hands them to `on_progress`.

Also `userFacingName` (the agent type, `Agent` for `worker` or none) and `renderToolUseMessage`
(the description, when the call has both a description and a prompt). Not ported, as React-only:
`userFacingNameBackgroundColor` (the agent's colour) and `renderToolUseTag` (the model tag).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.tool import Tool, find_tool_by_name
from app.tools.agent_tool.prompt import GENERAL_PURPOSE_AGENT_TYPE
from app.utils.collapse_read_search import (
    get_search_read_summary_text,
    get_tool_search_or_read_info,
)


def _search_or_read(message: Any, tools: Sequence[Tool], tool_uses: dict[str, Any], context: Any
                    ) -> tuple[bool, bool] | None:
    """`getSearchOrReadInfo`: (is_search, is_read) of a collapsible call or its result, else `None`."""
    call = None
    if isinstance(message, AIMessage) and message.tool_calls:
        call = message.tool_calls[0]
    elif isinstance(message, ToolMessage):
        call = tool_uses.get(message.tool_call_id)
    if call is None:
        return None
    info = get_tool_search_or_read_info(str(call.get("name")), call.get("args") or {}, tools, context)
    return (info.is_search, info.is_read) if info.is_collapsible else None


def extract_last_tool_info(messages: Sequence[Any], tools: Sequence[Tool], context: Any = None) -> str | None:
    """`extractLastToolInfo`."""
    tool_uses = {str(call.get("id")): call for m in messages if isinstance(m, AIMessage) for call in m.tool_calls}
    search_count = read_count = 0
    for message in reversed(messages):
        info = _search_or_read(message, tools, tool_uses, context)
        if info is None or not (info[0] or info[1]):
            break
        if isinstance(message, ToolMessage):  # results only, so a call is not counted twice
            if info[0]:
                search_count += 1
            else:
                read_count += 1
    if search_count + read_count >= 2:
        return get_search_read_summary_text(search_count, read_count, True)
    last_result = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
    if last_result is None:
        return None
    call = tool_uses.get(last_result.tool_call_id)
    if call is None:
        return None
    tool = find_tool_by_name(tools, str(call.get("name")))
    if tool is None:
        return str(call.get("name"))
    tool_input = call.get("args") or {}
    name = tool.user_facing_name(tool_input)
    summary = tool.get_tool_use_summary(tool_input, context)
    return f"{name}: {summary}" if summary else name


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`: the agent type, except `general-purpose` and `worker`, which read `Agent`."""
    subagent_type = (input or {}).get("subagent_type")
    if subagent_type and subagent_type != GENERAL_PURPOSE_AGENT_TYPE:
        # Display "worker" agents as "Agent" for cleaner UI
        if subagent_type == "worker":
            return "Agent"
        return str(subagent_type)
    return "Agent"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the description, or no row until both it and the prompt are there."""
    description, prompt = input.get("description"), input.get("prompt")
    if not description or not prompt:
        return None
    return str(description)


__all__ = ["extract_last_tool_info", "render_tool_use_message", "user_facing_name"]
