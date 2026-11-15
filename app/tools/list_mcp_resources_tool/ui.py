"""
How a ListMcpResources call is named and shown.

Ported from `reference/claude-code/src/tools/ListMcpResourcesTool/ListMcpResourcesTool.ts` ->
`userFacingName` and `UI.tsx` -> `renderToolUseMessage`.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`."""
    return "listMcpResources"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the server the call lists, or all of them."""
    server = input.get("server")
    return f'List MCP resources from server "{server}"' if server else "List all MCP resources"


__all__ = ["render_tool_use_message", "user_facing_name"]
