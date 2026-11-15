"""
A tool call's row in the REPL.

Ported from `reference/claude-code/src/components/messages/AssistantToolUseMessage.tsx` ->
`AssistantToolUseMessage` and its `renderToolUseMessage`: the dot, the tool's user-facing name, and its
input as the tool renders it, in parentheses: `⏺ Write(src/app.ts)`. A tool whose user-facing name is
`''`, or whose rendering is `null`, has no row; a rendering of `''` shows the name alone. An unknown tool
(a resumed conversation's old tool) has no row upstream; here it shows its raw name, so a call is never
silent.

*Divergences:* the row is text, so the loader's animation and colors are not drawn, and
`renderToolUseTag` (a timeout or model tag) is not ported. The context passed to the tool's rendering
stands in for upstream's process-wide cwd (a path is shown relative to the project).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.tool import Tool, find_tool_by_name

#: `BLACK_CIRCLE` (`constants/figures.ts`), the dot of a tool row.
TOOL_USE_DOT = "⏺"


def render_tool_use_row(call: Mapping[str, Any], tools: Sequence[Tool], context: Any = None,
                        *, verbose: bool = False) -> str | None:
    """The row for `call` (`{"name", "args", "id"}`), or `None` when the tool shows none."""
    name = str(call.get("name"))
    args = dict(call.get("args") or {})
    tool = find_tool_by_name(tools, name)
    if tool is None:
        return f"{TOOL_USE_DOT} {name}"
    user_facing_name = tool.user_facing_name(args)
    if user_facing_name == "":
        return None
    rendered = tool.render_tool_use_message(args, verbose, context)
    if rendered is None:
        return None
    return f"{TOOL_USE_DOT} {user_facing_name}({rendered})" if rendered else f"{TOOL_USE_DOT} {user_facing_name}"


__all__ = ["TOOL_USE_DOT", "render_tool_use_row"]
