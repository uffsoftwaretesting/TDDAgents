"""
What the `/mcp` menus share: a server as the list shows it, how a menu ends, and the session
pieces the menus act on.

Ported from `reference/claude-code/src/components/mcp/types.ts` -> `ServerInfo` (its
`StdioServerInfo` / `SSEServerInfo` / `HTTPServerInfo` variants as one record with `transport`) and
`MCPViewState`. The file is type-only upstream and is erased from the source snapshot; its shapes
are read from `MCPSettings.tsx`. The menus' `onComplete(result)` (the dialog closes and shows
`result`), `onCancel()` (back one level) and `onViewTools()` are the `MenuOutcome` a menu returns.

`McpPanelContext` carries what upstream's menus read from hooks and module state: the session's
MCP servers (`SessionMcp`: `useAppState(s => s.mcp)`, `useMcpReconnect`, `useMcpToggleEnabled`),
the project folder and policy directory (`describeMcpConfigFilePath`), and the OAuth calls
(`performMCPOAuthFlow`, `revokeServerTokens`, `ClaudeAuthProvider.tokens`), injectable for tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.mcp.auth import ClaudeAuthProvider, perform_mcp_oauth_flow, revoke_server_tokens
from app.services.mcp.types import McpServerConfig


@dataclass(frozen=True)
class ServerInfo:
    """`ServerInfo`: one server row; `is_authenticated` only for sse/http servers."""

    name: str
    client: Any
    scope: str
    transport: str
    config: McpServerConfig
    is_authenticated: bool | None = None


@dataclass(frozen=True, slots=True)
class Complete:
    """`onComplete(result)`: the dialog closes, `message` shown."""

    message: str


#: `onCancel()`: back to the previous view.
BACK = "back"
#: `onViewTools()`.
VIEW_TOOLS = "tools"

MenuOutcome = Complete | str


async def _stored_tokens(name: str, config: McpServerConfig) -> Any:
    """`new ClaudeAuthProvider(name, config).tokens()`."""
    return await ClaudeAuthProvider(name, config).tokens()


@dataclass
class McpPanelContext:
    """The session's MCP state and the calls the menus make."""

    mcp: Any
    cwd: str
    managed_dir: Path | None = None
    #: The session's log file, named in the list when a server failed (upstream's `--debug` hint).
    log_file: Path | None = None
    perform_oauth_flow: Callable[..., Awaitable[None]] = perform_mcp_oauth_flow
    revoke_server_tokens: Callable[..., Awaitable[None]] = revoke_server_tokens
    read_tokens: Callable[[str, McpServerConfig], Awaitable[Any]] = _stored_tokens


__all__ = ["BACK", "VIEW_TOOLS", "Complete", "McpPanelContext", "MenuOutcome", "ServerInfo"]
