"""
What the `/mcp` menus say after a reconnect.

Ported from `reference/claude-code/src/components/mcp/utils/reconnectHelpers.tsx` ->
`handleReconnectResult`, `handleReconnectError`, `ReconnectResult` (here
`ReconnectMessage`, since `ReconnectResult` names the reconnect's own result).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReconnectMessage:
    """`ReconnectResult`: the message, and whether the server is connected."""

    message: str
    success: bool


def handle_reconnect_result(result: Any, server_name: str) -> ReconnectMessage:
    """`handleReconnectResult`: the message for the reconnected server's state."""
    kind = getattr(result.client, "type", None)
    if kind == "connected":
        return ReconnectMessage(f"Reconnected to {server_name}.", True)
    if kind == "needs-auth":
        return ReconnectMessage(f"{server_name} requires authentication. Use the 'Authenticate' option.", False)
    if kind == "failed":
        return ReconnectMessage(f"Failed to reconnect to {server_name}.", False)
    return ReconnectMessage(f"Unknown result when reconnecting to {server_name}.", False)


def handle_reconnect_error(error: BaseException, server_name: str) -> str:
    """`handleReconnectError`."""
    return f"Error reconnecting to {server_name}: {error}"


__all__ = ["ReconnectMessage", "handle_reconnect_error", "handle_reconnect_result"]
