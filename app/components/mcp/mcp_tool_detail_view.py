"""
`/mcp`'s view of one MCP tool: its names, description and parameters.

Ported from `reference/claude-code/src/components/mcp/MCPToolDetailView.tsx` -> `MCPToolDetailView`.
"""

from __future__ import annotations

from typing import Any

from app.components.design_system.dialog import CANCELLED, DialogIO
from app.components.mcp.mcp_tool_list_view import tool_annotations, tool_display_name
from app.components.mcp.types import ServerInfo
from app.services.mcp.normalization import get_mcp_display_name


def _description(tool: Any) -> str:
    """`tool.description({})`; `Failed to load description` when it raises."""
    try:
        return str(tool.description({}))
    except Exception:  # noqa: BLE001 - upstream shows a placeholder instead
        return "Failed to load description"


def render_tool_detail(tool: Any, server: ServerInfo) -> list[str]:
    """The title with its annotations, the server, the names, the description and the parameters."""
    title = tool_display_name(tool, server.name) + "".join(f" [{a}]" for a in tool_annotations(tool))
    lines = [title, server.name, "", f"Tool name: {get_mcp_display_name(tool.name, server.name)}",
             f"Full name: {tool.name}"]
    description = _description(tool)
    if description:
        lines += ["", "Description:", description]
    schema = getattr(tool, "input_schema", None) or {}
    properties = schema.get("properties") or {}
    if properties:
        required = schema.get("required") or []
        lines += ["", "Parameters:"]
        for key, value in properties.items():
            kind = str(value["type"]) if isinstance(value, dict) and "type" in value else "unknown"
            line = f"  • {key}{' (required)' if key in required else ''}: {kind}"
            if isinstance(value, dict) and "description" in value:
                line += f" - {value['description']}"
            lines.append(line)
    return lines


async def show_tool_detail(io: DialogIO, tool: Any, server: ServerInfo) -> None:
    """The view, until Esc (or any line) goes back."""
    await io.ask("\n".join([*render_tool_detail(tool, server), "Esc to go back"]), lambda _line: CANCELLED,
                 "Esc to go back", CANCELLED)


__all__ = ["render_tool_detail", "show_tool_detail"]
