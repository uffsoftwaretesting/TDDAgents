"""
`/mcp`'s menu for a remote (sse / http) server: its status, authentication and URL, then View
tools, Authenticate / Re-authenticate, Clear authentication, Reconnect and Disable / Enable.

Ported from `reference/claude-code/src/components/mcp/MCPRemoteServerMenu.tsx` ->
`MCPRemoteServerMenu`, `handleAuthenticate`, `handleClearAuth`, `handleToggleEnabled`, the
authenticating and reconnecting views and the menu's options. "Claude Code" in its messages is
"TDDAgents".

Authenticate runs the MCP OAuth flow (`performMCPOAuthFlow`, `app/services/mcp/auth.py`): the
browser opens on the authorization URL, which is also shown to copy; once the flow waits for its
callback, a pasted redirect URL finishes it (the line typed); Esc aborts it and the menu comes back.
On success the server is reconnected and the dialog closes with the outcome; a failure is shown
in the menu (`Error: …`). Clear authentication revokes and forgets the server's tokens, closes its
connection and records it `failed` ("not connected").

Not ported: the claude.ai connector flows (`claudeai-proxy`), the copy-to-clipboard key, and the
analytics events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.components.design_system.dialog import CANCELLED, Cancelled, DialogIO, select
from app.components.mcp.capabilities_section import capabilities_line
from app.components.mcp.mcp_list_panel import CROSS, RADIO_OFF, TICK, TRIANGLE_UP_OUTLINE
from app.components.mcp.mcp_stdio_server_menu import capitalize, toggle_enabled
from app.components.mcp.types import BACK, VIEW_TOOLS, Complete, McpPanelContext, MenuOutcome, ServerInfo
from app.components.mcp.utils.reconnect_helpers import handle_reconnect_error, handle_reconnect_result
from app.services.mcp.auth import AuthenticationCancelledError
from app.services.mcp.utils import describe_mcp_config_file_path
from app.utils.abort_controller import AbortController

STILL_NEEDS_AUTH = (
    "Authentication successful, but server still requires authentication. You may need to manually restart "
    "TDDAgents.")
RECONNECT_FAILED = (
    "Authentication successful, but server reconnection failed. You may need to manually restart TDDAgents "
    "for the changes to take effect.")
COPY_URL = "If your browser doesn't open automatically, copy this URL manually"
PASTE_URL = "If the redirect page shows a connection error, paste the URL from your browser's address bar:"
RETURN_HERE = "Return here after authenticating in your browser. Press Esc to go back."


def is_effectively_authenticated(server: ServerInfo, tools_count: int) -> bool:
    """OAuth tokens, or connected with tools (working through some other authentication)."""
    return bool(server.is_authenticated) or (getattr(server.client, "type", None) == "connected" and tools_count > 0)


def remote_status(client: object) -> str:
    """The menu's `Status:` value."""
    kind = getattr(client, "type", None)
    if kind == "disabled":
        return f"{RADIO_OFF} disabled"
    if kind == "connected":
        return f"{TICK} connected"
    if kind == "pending":
        return f"{RADIO_OFF} connecting…"
    if kind == "needs-auth":
        return f"{TRIANGLE_UP_OUTLINE} needs authentication"
    return f"{CROSS} failed"


def render_remote_server(
    context: McpPanelContext, server: ServerInfo, tools_count: int, error: str | None = None
) -> list[str]:
    """The menu's heading, details and the last error."""
    auth = (f"{TICK} authenticated" if is_effectively_authenticated(server, tools_count)
            else f"{CROSS} not authenticated")
    lines = [f"{capitalize(server.name)} MCP Server", "", f"Status: {remote_status(server.client)}",
             f"Auth: {auth}", f"URL: {server.config.url or ''}",
             f"Config location: {describe_mcp_config_file_path(server.scope, context.cwd, context.managed_dir)}"]
    connected = getattr(server.client, "type", None) == "connected"
    if connected:
        lines.append(capabilities_line(tools_count, 0, 0))
    if connected and tools_count > 0:
        lines.append(f"Tools: {tools_count} tools")
    if error:
        lines += ["", f"Error: {error}"]
    return lines


def remote_menu_options(server: ServerInfo, tools_count: int) -> list[tuple[str, str]]:
    """The menu's options, `(label, value)`, in upstream's order."""
    kind = getattr(server.client, "type", None)
    options: list[tuple[str, str]] = []
    if kind == "disabled":
        options.append(("Enable", "toggle-enabled"))
    if kind == "connected" and tools_count > 0:
        options.append(("View tools", "tools"))
    if is_effectively_authenticated(server, tools_count):
        options += [("Re-authenticate", "reauth"), ("Clear authentication", "clear-auth")]
    else:
        options.append(("Authenticate", "auth"))
    if kind != "disabled":
        if kind != "needs-auth":
            options.append(("Reconnect", "reconnectMcpServer"))
        options.append(("Disable", "toggle-enabled"))
    return options


class _AuthView:
    """The authenticating view: the URL to copy, then the paste prompt once the flow waits for its callback."""

    def __init__(self, io: DialogIO) -> None:
        self.io = io
        self.url: str | None = None
        self.submit: Callable[[str], None] | None = None
        self._paste_shown = False

    def on_authorization_url(self, url: str) -> None:
        self.url = url
        self.io.write(f"{COPY_URL}\n{url}")
        self._show_paste()

    def on_waiting_for_callback(self, submit: Callable[[str], None]) -> None:
        self.submit = submit
        self._show_paste()

    def _show_paste(self) -> None:
        if self.url and self.submit is not None and not self._paste_shown:
            self._paste_shown = True
            self.io.write(PASTE_URL)


async def authenticate(
    io: DialogIO, context: McpPanelContext, server: ServerInfo, tools_count: int
) -> Complete | str | None:
    """
    `handleAuthenticate`: the dialog's result on success, the error to show in the menu, or
    `None` when the user went back (Esc) or the flow was cancelled.
    """
    effectively = is_effectively_authenticated(server, tools_count)
    oauth = server.config.oauth
    auth_copy = (" Authenticating via your identity provider" if oauth is not None and oauth.xaa
                 else " A browser window will open for authentication")
    io.write(f"Authenticating with {server.name}…\n{auth_copy}")
    controller = AbortController()
    view = _AuthView(io)
    try:
        if server.is_authenticated:
            await context.revoke_server_tokens(server.name, server.config, preserve_step_up_state=True)
        flow: asyncio.Future[None] = asyncio.ensure_future(context.perform_oauth_flow(
            server.name, server.config, view.on_authorization_url, controller.signal,
            on_waiting_for_callback=view.on_waiting_for_callback))
        if not await _wait_for_flow(io, flow, controller, view):
            return None
        await flow
        result = await context.mcp.reconnect(server.name, server.config)
    except AuthenticationCancelledError:
        return None
    except Exception as error:  # noqa: BLE001 - upstream shows the failure in the menu
        return str(error)
    kind = getattr(result.client, "type", None)
    if kind == "connected":
        verb = "Reconnected" if effectively else "Connected"
        return Complete(f"Authentication successful. {verb} to {server.name}.")
    if kind == "needs-auth":
        return Complete(STILL_NEEDS_AUTH)
    return Complete(RECONNECT_FAILED)


async def _wait_for_flow(
    io: DialogIO, flow: asyncio.Future[None], controller: AbortController, view: _AuthView
) -> bool:
    """Read pasted callback URLs until the flow ends; `False` when Esc aborted it. A dialog torn down aborts it."""
    try:
        return await _read_callbacks(io, flow, controller, view)
    except asyncio.CancelledError:
        controller.abort()
        raise


async def _read_callbacks(
    io: DialogIO, flow: asyncio.Future[None], controller: AbortController, view: _AuthView
) -> bool:
    while not flow.done():
        asking: asyncio.Future[Any] = asyncio.ensure_future(
            io.ask(RETURN_HERE, lambda line: line.strip() or None, RETURN_HERE, CANCELLED))
        await asyncio.wait({asking, flow}, return_when=asyncio.FIRST_COMPLETED)
        if flow.done():
            # The flow ended (the browser's callback, or a failure): a line typed meanwhile is moot.
            asking.cancel()
            await asyncio.gather(asking, return_exceptions=True)
            break
        answer = asking.result()
        if answer is CANCELLED:
            controller.abort()
            await asyncio.gather(flow, return_exceptions=True)
            return False
        if view.submit is not None:
            view.submit(answer)
    return True


async def clear_auth(context: McpPanelContext, server: ServerInfo) -> Complete:
    """`handleClearAuth`: tokens revoked, the connection closed, the server not connected."""
    await context.revoke_server_tokens(server.name, server.config)
    await context.mcp.clear_server(server.name, server.config)
    return Complete(f"Authentication cleared for {server.name}.")


async def run_remote_server_menu(
    io: DialogIO, context: McpPanelContext, server: ServerInfo, tools_count: int
) -> MenuOutcome:
    """`MCPRemoteServerMenu`: the menu until it closes or goes back."""
    error: str | None = None
    while True:
        options = remote_menu_options(server, tools_count)
        picked = await select(io, render_remote_server(context, server, tools_count, error),
                              [label for label, _ in options], "go back")
        if isinstance(picked, Cancelled):
            return BACK
        value = options[picked][1]
        if value == "tools":
            return VIEW_TOOLS
        if value in ("auth", "reauth"):
            outcome = await authenticate(io, context, server, tools_count)
            if isinstance(outcome, Complete):
                return outcome
            error = outcome
            continue
        if value == "clear-auth":
            return await clear_auth(context, server)
        if value == "reconnectMcpServer":
            io.write("\n".join([f"Connecting to {server.name}…", " Establishing connection to MCP server",
                                "This may take a few moments."]))
            try:
                result = await context.mcp.reconnect(server.name, server.config)
            except Exception as reconnect_error:  # noqa: BLE001 - upstream reports it as the dialog's result
                return Complete(handle_reconnect_error(reconnect_error, server.name))
            return Complete(handle_reconnect_result(result, server.name).message)
        return await toggle_enabled(context, server)


__all__ = [
    "COPY_URL",
    "PASTE_URL",
    "RECONNECT_FAILED",
    "RETURN_HERE",
    "STILL_NEEDS_AUTH",
    "authenticate",
    "clear_auth",
    "is_effectively_authenticated",
    "remote_menu_options",
    "remote_status",
    "render_remote_server",
    "run_remote_server_menu",
]
