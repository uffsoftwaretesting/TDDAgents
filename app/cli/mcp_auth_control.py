"""
The stream-json control requests that authenticate an MCP server, finish its OAuth flow from a
pasted callback URL, or clear its credentials.

Ported from `reference/claude-code/src/cli/print.ts` -> `runHeadless`'s control-message loop:
the `mcp_authenticate` (L3310-3462), `mcp_oauth_callback_url` (L3463-3512) and
`mcp_clear_auth` (L3651-3698) branches, and the per-session maps they share
(`activeOAuthFlows`, `oauthCallbackSubmitters`, `oauthManualCallbackUsed`,
`oauthAuthPromises`), here `McpOAuthFlows`.

Each handler returns what upstream sends: `McpControlSuccess(response)` for
`sendControlResponseSuccess(message, response)` (`response` is `None` where upstream passes
none) or `McpControlError(error)` for `sendControlResponseError(message, error)`. What
`print.ts` reads from its closure is injected:

* `find_config(server_name)`: `getMcpConfigByName(serverName) ?? mcpClients.find(...)?.config
  ?? appState.mcp.clients.find(...)?.config ?? null`;
* `reconnect(server_name, config)`: `reconnectMcpServerImpl`
  (`mcp_connection_manager.reconnect_mcp_server_impl` bound to the session's cwd);
* `apply_reconnect(server_name, result)`: the `setAppState` update of `mcp.clients`,
  `mcp.tools` (the server's `mcp__<server>__` tools replaced), `mcp.commands` and
  `mcp.resources`, and for `mcp_authenticate` the `dynamicMcpState` update too;
* `is_server_disabled(server_name)`: `isMcpServerDisabled`.

`mcp_authenticate` answers as soon as the flow has an authorization URL (`requiresUserAction:
true`, with the URL; the browser is not opened) or has finished without one
(`requiresUserAction: false`). The flow then continues in the background: on success it
reconnects the server unless the server was disabled meanwhile or the URL was pasted through
`mcp_oauth_callback_url` (the client reconnects then, with `mcp_reconnect`), and it always
forgets its entries if it is still the server's active flow. A new `mcp_authenticate` aborts the
server's previous flow.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.services.mcp.auth import perform_mcp_oauth_flow, revoke_server_tokens
from app.services.mcp.mcp_connection_manager import ReconnectResult
from app.services.mcp.types import McpServerConfig
from app.utils.abort_controller import AbortController

logger = logging.getLogger(__name__)

INVALID_CALLBACK_URL_ERROR = (
    "Invalid callback URL: missing authorization code. Please paste the full redirect URL including the code "
    "parameter."
)

FindConfig = Callable[[str], McpServerConfig | None]
Reconnect = Callable[[str, McpServerConfig], Awaitable[ReconnectResult]]
ApplyReconnect = Callable[[str, ReconnectResult], None]
IsServerDisabled = Callable[[str], bool]
PerformOAuthFlow = Callable[..., Awaitable[None]]
RevokeServerTokens = Callable[[str, McpServerConfig], Awaitable[None]]


@dataclass(frozen=True)
class McpControlSuccess:
    """`sendControlResponseSuccess(message, response)`."""

    response: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpControlError:
    """`sendControlResponseError(message, error)`."""

    error: str


McpControlOutcome = McpControlSuccess | McpControlError


@dataclass
class McpOAuthFlows:
    """The session's in-flight MCP OAuth flows, by server name."""

    active_oauth_flows: dict[str, AbortController] = field(default_factory=dict)
    oauth_callback_submitters: dict[str, Callable[[str], None]] = field(default_factory=dict)
    oauth_manual_callback_used: set[str] = field(default_factory=set)
    oauth_auth_promises: dict[str, asyncio.Future[None]] = field(default_factory=dict)
    #: The background completions (`void fullFlowPromise`), kept referenced until they end.
    background: set[asyncio.Task[None]] = field(default_factory=set)


def _type_label(config: McpServerConfig) -> str:
    """`${config.type}`: an entry without `type` renders as JavaScript's `undefined`."""
    return config.type if config.type is not None else "undefined"


async def handle_mcp_authenticate(
    server_name: str,
    *,
    flows: McpOAuthFlows,
    find_config: FindConfig,
    reconnect: Reconnect,
    apply_reconnect: ApplyReconnect,
    is_server_disabled: IsServerDisabled,
    perform_oauth_flow: PerformOAuthFlow = perform_mcp_oauth_flow,
) -> McpControlOutcome:
    """`mcp_authenticate`: start the server's OAuth flow and answer with its authorization URL."""
    config = find_config(server_name)
    if config is None:
        return McpControlError(f"Server not found: {server_name}")
    if config.type not in ("sse", "http"):
        return McpControlError(f'Server type "{_type_label(config)}" does not support OAuth authentication')
    try:
        previous = flows.active_oauth_flows.get(server_name)
        if previous is not None:
            previous.abort()
        controller = AbortController()
        flows.active_oauth_flows[server_name] = controller

        loop = asyncio.get_running_loop()
        auth_url: asyncio.Future[str | None] = loop.create_future()

        def on_authorization_url(url: str) -> None:
            if not auth_url.done():
                auth_url.set_result(url)

        def on_waiting_for_callback(submit: Callable[[str], None]) -> None:
            flows.oauth_callback_submitters[server_name] = submit

        oauth_promise: asyncio.Future[None] = asyncio.ensure_future(perform_oauth_flow(
            server_name, config, on_authorization_url, controller.signal,
            skip_browser_open=True, on_waiting_for_callback=on_waiting_for_callback,
        ))

        def on_flow_done(done: asyncio.Future[None]) -> None:
            if auth_url.done():
                return
            if done.cancelled():
                auth_url.cancel()
            elif done.exception() is not None:
                auth_url.set_exception(done.exception())  # type: ignore[arg-type]
            else:
                auth_url.set_result(None)

        oauth_promise.add_done_callback(on_flow_done)
        url = await asyncio.shield(auth_url)
        outcome = McpControlSuccess(
            {"authUrl": url, "requiresUserAction": True} if url else {"requiresUserAction": False})
        flows.oauth_auth_promises[server_name] = oauth_promise

        async def full_flow() -> None:
            try:
                await asyncio.shield(oauth_promise)
                if is_server_disabled(server_name):
                    return
                if server_name in flows.oauth_manual_callback_used:
                    return
                result = await reconnect(server_name, config)
                apply_reconnect(server_name, result)
            except Exception as exc:  # noqa: BLE001 - upstream logs the failed background flow
                logger.error("MCP OAuth failed for %s: %s", server_name, exc)
            finally:
                if flows.active_oauth_flows.get(server_name) is controller:
                    flows.active_oauth_flows.pop(server_name, None)
                    flows.oauth_callback_submitters.pop(server_name, None)
                    flows.oauth_manual_callback_used.discard(server_name)
                    flows.oauth_auth_promises.pop(server_name, None)

        task = loop.create_task(full_flow())
        flows.background.add(task)
        task.add_done_callback(flows.background.discard)
        return outcome
    except Exception as exc:  # noqa: BLE001 - upstream: sendControlResponseError(errorMessage(error))
        return McpControlError(str(exc))


def _has_code_or_error(callback_url: str) -> bool:
    """`new URL(callbackUrl)` parses and carries `code` or `error`."""
    try:
        parts = urlsplit(callback_url)
    except ValueError:
        return False
    if not parts.scheme or not (parts.netloc or parts.path):
        return False
    query = parse_qs(parts.query, keep_blank_values=True)
    return "code" in query or "error" in query


async def handle_mcp_oauth_callback_url(
    server_name: str, callback_url: str, *, flows: McpOAuthFlows
) -> McpControlOutcome:
    """`mcp_oauth_callback_url`: hand the pasted redirect URL to the flow and wait for its token exchange."""
    submit = flows.oauth_callback_submitters.get(server_name)
    if submit is None:
        return McpControlError(f"No active OAuth flow for server: {server_name}")
    if not _has_code_or_error(callback_url):
        return McpControlError(INVALID_CALLBACK_URL_ERROR)
    flows.oauth_manual_callback_used.add(server_name)
    submit(callback_url)
    auth_promise = flows.oauth_auth_promises.get(server_name)
    if auth_promise is None:
        return McpControlSuccess()
    try:
        await asyncio.shield(auth_promise)
    except Exception as exc:  # noqa: BLE001 - upstream reports the failed exchange
        # `error instanceof Error ? error.message : 'OAuth authentication failed'`: every
        # exception here is an `Error`, so its message, even an empty one.
        return McpControlError(str(exc))
    return McpControlSuccess()


async def handle_mcp_clear_auth(
    server_name: str,
    *,
    find_config: FindConfig,
    reconnect: Reconnect,
    apply_reconnect: ApplyReconnect,
    revoke: RevokeServerTokens = revoke_server_tokens,
) -> McpControlOutcome:
    """`mcp_clear_auth`: revoke and forget the server's tokens, then reconnect it."""
    config = find_config(server_name)
    if config is None:
        return McpControlError(f"Server not found: {server_name}")
    if config.type not in ("sse", "http"):
        return McpControlError(f'Cannot clear auth for server type "{_type_label(config)}"')
    await revoke(server_name, config)
    result = await reconnect(server_name, config)
    apply_reconnect(server_name, result)
    return McpControlSuccess({})


__all__ = [
    "INVALID_CALLBACK_URL_ERROR",
    "ApplyReconnect",
    "FindConfig",
    "IsServerDisabled",
    "McpControlError",
    "McpControlOutcome",
    "McpControlSuccess",
    "McpOAuthFlows",
    "Reconnect",
    "handle_mcp_authenticate",
    "handle_mcp_clear_auth",
    "handle_mcp_oauth_callback_url",
]
