"""
`/mcp`'s list of one server's tools, each with its annotations.

Ported from `reference/claude-code/src/components/mcp/MCPToolListView.tsx` -> `MCPToolListView`.
A tool is picked by its number (its detail view), Esc goes back to the server's menu. The
`open-world` annotation is not shown: the run's MCP tools do not keep `openWorldHint`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.components.design_system.dialog import CANCELLED, Cancelled, DialogIO, select
from app.components.mcp.types import ServerInfo
from app.services.mcp.normalization import extract_mcp_tool_display_name, get_mcp_display_name
from app.services.mcp.utils import filter_tools_by_server
from app.utils.string_utils import plural


def server_tools(server: ServerInfo, tools: Sequence[Any]) -> list[Any]:
    """The server's tools; none unless it is connected."""
    if getattr(server.client, "type", None) != "connected":
        return []
    return filter_tools_by_server(tools, server.name)


def tool_display_name(tool: Any, server_name: str) -> str:
    """The tool's name without its server: its user-facing name stripped, else the bare MCP name."""
    name = get_mcp_display_name(tool.name, server_name)
    user_facing = getattr(tool, "user_facing_name", None)
    return extract_mcp_tool_display_name(user_facing({}) if callable(user_facing) else name)


def _flag(tool: Any, method: str) -> bool:
    check = getattr(tool, method, None)
    return bool(check({})) if callable(check) else False


def tool_annotations(tool: Any) -> list[str]:
    """`read-only`, `destructive`: the hints the server gave."""
    return [label for label, method in (("read-only", "is_read_only"), ("destructive", "is_destructive"))
            if _flag(tool, method)]


async def select_tool(io: DialogIO, server: ServerInfo, tools: Sequence[Any]) -> int | Cancelled:
    """The server's tools; the index picked, or `CANCELLED` (Esc, or nothing to pick)."""
    listed = server_tools(server, tools)
    heading = [f"Tools for {server.name}", f"{len(listed)} {plural(len(listed), 'tool')}"]
    if not listed:
        await io.ask("\n".join([*heading, "No tools available", "Esc to go back"]), lambda _line: None,
                     "Esc to go back", CANCELLED)
        return CANCELLED
    return await select(io, heading, [tool_display_name(t, server.name) for t in listed], "go back",
                        [", ".join(tool_annotations(t)) or None for t in listed])


__all__ = ["select_tool", "server_tools", "tool_annotations", "tool_display_name"]
