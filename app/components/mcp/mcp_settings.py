"""
`/mcp`'s dialog: the server list, a server's menu, its tools and a tool's detail.

Ported from `reference/claude-code/src/components/mcp/MCPSettings.tsx` -> `MCPSettings` (its
`prepareServers`, the view states `list` / `server-menu` / `server-tools` /
`server-tool-detail`, and the empty-list result). The servers are the session's (`SessionMcp`:
configured, SDK and dynamic), sorted by name, without `ide`. An sse or http server is
authenticated when it has stored OAuth tokens or is connected with tools.

Upstream's empty-list result also says to run `/doctor`, `claude mcp --help` or read Claude Code's
documentation; TDDAgents has none of them, so it says only that no server is configured. Not
ported: the agent-only servers (`agent-server-menu`) and claude.ai connectors.
"""

from __future__ import annotations

from typing import Any

from app.components.design_system.dialog import Cancelled, DialogIO
from app.components.mcp.mcp_list_panel import DISMISSED, select_server
from app.components.mcp.mcp_remote_server_menu import run_remote_server_menu
from app.components.mcp.mcp_stdio_server_menu import run_stdio_server_menu
from app.components.mcp.mcp_tool_detail_view import show_tool_detail
from app.components.mcp.mcp_tool_list_view import select_tool, server_tools
from app.components.mcp.types import BACK, Complete, McpPanelContext, ServerInfo
from app.services.mcp.utils import filter_tools_by_server

NO_SERVERS = "No MCP servers configured."


async def prepare_servers(context: McpPanelContext) -> list[ServerInfo]:
    """`prepareServers`: each connection as a list row."""
    tools = context.mcp.all_tools()
    servers = []
    for client in sorted((c for c in context.mcp.all_clients() if c.name != "ide"), key=lambda c: c.name):
        kind = client.config.type
        is_authenticated = None
        if kind in ("sse", "http"):
            tokens = await context.read_tokens(client.name, client.config)
            connected = getattr(client, "type", None) == "connected"
            is_authenticated = bool(tokens) or (connected and bool(filter_tools_by_server(tools, client.name)))
        transport = kind if kind in ("sse", "http") else "stdio"
        servers.append(ServerInfo(name=client.name, client=client, scope=client.config.scope, transport=transport,
                                  config=client.config, is_authenticated=is_authenticated))
    return servers


async def _server_menu(io: DialogIO, context: McpPanelContext, server: ServerInfo) -> Any:
    tools_count = len(filter_tools_by_server(context.mcp.all_tools(), server.name))
    if server.transport == "stdio":
        return await run_stdio_server_menu(io, context, server, tools_count)
    return await run_remote_server_menu(io, context, server, tools_count)


async def _tools(io: DialogIO, context: McpPanelContext, server: ServerInfo) -> None:
    """`server-tools` and `server-tool-detail`, until Esc goes back to the server's menu."""
    while True:
        index = await select_tool(io, server, context.mcp.all_tools())
        if isinstance(index, Cancelled):
            return
        listed = server_tools(server, context.mcp.all_tools())
        if index < len(listed):
            await show_tool_detail(io, listed[index], server)


async def run_mcp_settings(io: DialogIO, context: McpPanelContext) -> str:
    """`MCPSettings`: the dialog, until it closes; its result is what the REPL shows."""
    servers = await prepare_servers(context)
    if not servers:
        return NO_SERVERS
    while True:
        picked = await select_server(io, servers, context.cwd, context.managed_dir)
        if isinstance(picked, Cancelled):
            return DISMISSED
        server = picked
        while True:
            outcome = await _server_menu(io, context, server)
            if isinstance(outcome, Complete):
                return outcome.message
            if outcome == BACK:
                break
            await _tools(io, context, server)
            server = next((s for s in await prepare_servers(context) if s.name == server.name), server)
        servers = await prepare_servers(context)
        if not servers:
            return NO_SERVERS


__all__ = ["NO_SERVERS", "prepare_servers", "run_mcp_settings"]
