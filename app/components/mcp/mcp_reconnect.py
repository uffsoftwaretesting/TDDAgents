"""
`/mcp reconnect <server>`: reconnect one server and say how it went.

Ported from `reference/claude-code/src/components/mcp/MCPReconnect.tsx` -> `MCPReconnect`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


async def mcp_reconnect(mcp: Any, server_name: str, write: Callable[[str], None]) -> str:
    """The reconnect's progress written, then its outcome (the command's result)."""
    server = mcp.find_client(server_name)
    if server is None:
        return f'MCP server "{server_name}" not found'
    write(f"Reconnecting to {server_name}\n Establishing connection to MCP server")
    try:
        result = await mcp.reconnect(server_name, server.config)
    except Exception as error:  # noqa: BLE001 - upstream reports it as the command's result
        return f"Error: {error}"
    kind = getattr(result.client, "type", None)
    if kind == "connected":
        return f"Successfully reconnected to {server_name}"
    if kind == "needs-auth":
        return f"{server_name} requires authentication. Use /mcp to authenticate."
    return f"Failed to reconnect to {server_name}"


__all__ = ["mcp_reconnect"]
