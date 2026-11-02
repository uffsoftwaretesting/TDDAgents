"""
The `/mcp` command: the MCP dialog, or one of its argument forms.

Ported from `reference/claude-code/src/commands/mcp/mcp.tsx` -> `call`, `MCPToggle`, and
`commands/mcp/index.ts` (the command's description and argument hint, registered in
`app/commands.py`). Upstream keeps it in `src/commands/mcp/`; here `app/commands` is a module,
not a package, so the command's body lives beside the dialog it opens.

* `/mcp` (and `/mcp no-redirect`): the dialog (`app/components/mcp/mcp_settings.py`);
* `/mcp reconnect <server>`: that server reconnected (`mcp_reconnect.py`);
* `/mcp enable [server|all]`, `/mcp disable [server|all]`: with `all` (the default), every server
  not already in that state is toggled; with a name, that server is toggled, as upstream's
  `toggleMcpServer` does whatever its current state (so `/mcp enable x` on an enabled `x`
  disables it).

The result is the text the REPL shows when the command ends.
"""

from __future__ import annotations

from typing import Any

from app.components.design_system.dialog import DialogIO
from app.components.mcp.mcp_reconnect import mcp_reconnect
from app.components.mcp.mcp_settings import run_mcp_settings
from app.components.mcp.types import McpPanelContext

DESCRIPTION = "Manage MCP servers"
ARGUMENT_HINT = "[enable|disable [server-name]]"


async def mcp_toggle(mcp: Any, action: str, target: str) -> str:
    """`MCPToggle`: the servers toggled, and what happened."""
    enabling = action == "enable"
    clients = [c for c in mcp.all_clients() if c.name != "ide"]
    if target == "all":
        to_toggle = [c for c in clients if (c.type == "disabled") == enabling]
    else:
        to_toggle = [c for c in clients if c.name == target]
    if not to_toggle:
        if target == "all":
            return f"All MCP servers are already {'enabled' if enabling else 'disabled'}"
        return f'MCP server "{target}" not found'
    for client in to_toggle:
        if client.type == "disabled":
            await mcp.enable(client.name, client.config)
        else:
            await mcp.disable(client.name, client.config)
    if target == "all":
        return f"{'Enabled' if enabling else 'Disabled'} {len(to_toggle)} MCP server(s)"
    return f'MCP server "{target}" {"enabled" if enabling else "disabled"}'


async def call(io: DialogIO, context: McpPanelContext, args: str = "") -> str:
    """`call(onDone, context, args)`: the command's result."""
    parts = args.split()
    if parts:
        if parts[0] == "no-redirect":
            return await run_mcp_settings(io, context)
        if parts[0] == "reconnect" and len(parts) > 1:
            return await mcp_reconnect(context.mcp, " ".join(parts[1:]), io.write)
        if parts[0] in ("enable", "disable"):
            return await mcp_toggle(context.mcp, parts[0], " ".join(parts[1:]) if len(parts) > 1 else "all")
    return await run_mcp_settings(io, context)


__all__ = ["ARGUMENT_HINT", "DESCRIPTION", "call", "mcp_toggle"]
