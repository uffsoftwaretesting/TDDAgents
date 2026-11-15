"""
How a ReadMcpResource call is named and shown.

Ported from `reference/claude-code/src/tools/ReadMcpResourceTool/UI.tsx` -> `userFacingName`,
`renderToolUseMessage`.
"""

from __future__ import annotations

from typing import Any


def user_facing_name(input: dict[str, Any] | None) -> str:
    """`userFacingName`."""
    return "readMcpResource"


def render_tool_use_message(input: dict[str, Any], verbose: bool) -> str | None:
    """`renderToolUseMessage`: the resource and its server, or no row until both are there."""
    uri, server = input.get("uri"), input.get("server")
    if not uri or not server:
        return None
    return f'Read resource "{uri}" from server "{server}"'


__all__ = ["render_tool_use_message", "user_facing_name"]
