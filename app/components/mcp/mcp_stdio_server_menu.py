"""
`/mcp`'s menu for a local (stdio) server: its status and command, then View tools, Reconnect and
Disable / Enable.

Ported from `reference/claude-code/src/components/mcp/MCPStdioServerMenu.tsx` ->
`MCPStdioServerMenu`, `handleToggleEnabled`. A server whose transport is neither sse nor http
(stdio, ws, an SDK host's) gets this menu, as upstream's `MCPSettings` routes it. The prompts and
resources counts are 0: the run keeps neither MCP prompts (`mcp.commands`) nor the fetched
resources (`mcp.resources`).
"""

from __future__ import annotations

from app.components.design_system.dialog import Cancelled, DialogIO, select
from app.components.mcp.capabilities_section import capabilities_line
from app.components.mcp.mcp_list_panel import CROSS, RADIO_OFF, TICK
from app.components.mcp.types import BACK, VIEW_TOOLS, Complete, McpPanelContext, MenuOutcome, ServerInfo
from app.components.mcp.utils.reconnect_helpers import handle_reconnect_error, handle_reconnect_result
from app.services.mcp.utils import describe_mcp_config_file_path


def capitalize(text: str) -> str:
    """`capitalize` (`utils/stringUtils.ts`): the first character upper-cased."""
    return text[:1].upper() + text[1:]


def stdio_status(client: object) -> str:
    """The menu's `Status:` value."""
    kind = getattr(client, "type", None)
    if kind == "disabled":
        return f"{RADIO_OFF} disabled"
    if kind == "connected":
        return f"{TICK} connected"
    if kind == "pending":
        return f"{RADIO_OFF} connecting…"
    return f"{CROSS} failed"


def config_scope(context: McpPanelContext, server: ServerInfo) -> str:
    """`getMcpConfigByName(server.name)?.scope ?? 'dynamic'`."""
    from app.services.mcp.config import get_mcp_config_by_name

    locations = getattr(context.mcp, "settings_locations", None)
    config = get_mcp_config_by_name(server.name, context.cwd, locations()) if locations is not None else None
    return config.scope if config is not None else "dynamic"


def render_stdio_server(context: McpPanelContext, server: ServerInfo, tools_count: int) -> list[str]:
    """The menu's heading and details."""
    lines = [f"{capitalize(server.name)} MCP Server", "", f"Status: {stdio_status(server.client)}",
             f"Command: {server.config.command or ''}"]
    if server.config.args:
        lines.append(f"Args: {' '.join(server.config.args)}")
    lines.append(f"Config location: "
                 f"{describe_mcp_config_file_path(config_scope(context, server), context.cwd, context.managed_dir)}")
    connected = getattr(server.client, "type", None) == "connected"
    if connected:
        lines.append(capabilities_line(tools_count, 0, 0))
    if connected and tools_count > 0:
        lines.append(f"Tools: {tools_count} tools")
    return lines


def stdio_menu_options(server: ServerInfo, tools_count: int) -> list[tuple[str, str]]:
    """The menu's options, `(label, value)`."""
    disabled = getattr(server.client, "type", None) == "disabled"
    options: list[tuple[str, str]] = []
    if not disabled and tools_count > 0:
        options.append(("View tools", "tools"))
    if not disabled:
        options.append(("Reconnect", "reconnectMcpServer"))
    options.append(("Disable" if not disabled else "Enable", "toggle-enabled"))
    return options


async def toggle_enabled(context: McpPanelContext, server: ServerInfo) -> MenuOutcome:
    """`handleToggleEnabled`: toggled, back to the list; a failure closes the dialog with it."""
    was_enabled = getattr(server.client, "type", None) != "disabled"
    try:
        if was_enabled:
            await context.mcp.disable(server.name, server.config)
        else:
            await context.mcp.enable(server.name, server.config)
    except Exception as error:  # noqa: BLE001 - upstream reports the failure as the dialog's result
        action = "disable" if was_enabled else "enable"
        return Complete(f"Failed to {action} MCP server '{server.name}': {error}")
    return BACK


async def run_stdio_server_menu(
    io: DialogIO, context: McpPanelContext, server: ServerInfo, tools_count: int
) -> MenuOutcome:
    """`MCPStdioServerMenu`: what the user picked, as the menu's outcome."""
    options = stdio_menu_options(server, tools_count)
    picked = await select(io, render_stdio_server(context, server, tools_count), [label for label, _ in options],
                          "go back")
    if isinstance(picked, Cancelled):
        return BACK
    value = options[picked][1]
    if value == "tools":
        return VIEW_TOOLS
    if value == "reconnectMcpServer":
        io.write("\n".join([f"Reconnecting to {server.name}", " Restarting MCP server process",
                            "This may take a few moments."]))
        try:
            result = await context.mcp.reconnect(server.name, server.config)
        except Exception as error:  # noqa: BLE001 - upstream reports the failure as the dialog's result
            return Complete(handle_reconnect_error(error, server.name))
        return Complete(handle_reconnect_result(result, server.name).message)
    return await toggle_enabled(context, server)


__all__ = [
    "capitalize",
    "config_scope",
    "render_stdio_server",
    "run_stdio_server_menu",
    "stdio_menu_options",
    "stdio_status",
    "toggle_enabled",
]
