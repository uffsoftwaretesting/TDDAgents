"""
The pseudo-tool offered in place of a `needs-auth` MCP server's tools: it starts the server's
OAuth flow from inside a turn.

Ported from `reference/claude-code/src/tools/McpAuthTool/McpAuthTool.ts` -> `createMcpAuthTool`,
`McpAuthOutput`, `getConfigUrl`.

`mcp__<server>__authenticate` takes no input and is always allowed. A call starts
`perform_mcp_oauth_flow` without opening a browser and answers as soon as the flow has an
authorization URL (`status: "auth_url"`, the URL for the user to open), or has finished without
one (silent auth). A flow that fails before either answers `status: "error"`. A claude.ai
connector or a transport other than sse/http answers `status: "unsupported"`.

The flow then continues in the background. When it succeeds, the needs-auth cache is cleared
and the server is reconnected, and the result is applied to the run. Both are injected, because
upstream does them through the session's module state (`reconnectMcpServerImpl` reads the cwd,
`setAppState` swaps the server's `mcp__<server>__` tools, this pseudo-tool among them, for the
reconnected ones):

* `reconnect(server_name, config) -> ReconnectResult`: `reconnectMcpServerImpl`;
* `apply_reconnect(server_name, result)`: the `setAppState` update of the run's MCP clients,
  tools, commands and resources.

`McpConnectionManager` builds this tool with its own `reconnect` and `apply_reconnect`. The
background continuation is kept in `background` (upstream's `void`), so it is not
garbage-collected and its owner can cancel it when the session ends. A failed continuation is
logged, never raised.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.services.mcp.auth import perform_mcp_oauth_flow
from app.services.mcp.client import clear_mcp_auth_cache
from app.services.mcp.normalization import build_mcp_tool_name
from app.services.mcp.types import McpServerConfig
from app.tool import BuiltTool, PermissionResult, ToolResult, build_tool
from app.tools.mcp_tool.mcp_tool import McpBuiltTool
from app.utils.abort_controller import AbortController
from app.utils.frontmatter_parser import parse_markdown_frontmatter, render_prompt

if TYPE_CHECKING:
    from app.services.mcp.mcp_connection_manager import ReconnectResult

    #: `reconnectMcpServerImpl(server_name, config)`.
    Reconnect = Callable[[str, McpServerConfig], Awaitable[ReconnectResult]]
    #: The `setAppState` update with the reconnect's result.
    ApplyReconnect = Callable[[str, ReconnectResult], None]

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).with_name("prompt.md")
#: `createMcpAuthTool` -> `maxResultSizeChars`.
MCP_AUTH_MAX_RESULT_SIZE_CHARS = 10_000
#: The tool half of `mcp__<server>__authenticate`.
MCP_AUTH_TOOL_NAME = "authenticate"

#: `performMCPOAuthFlow(server_name, config, on_authorization_url, signal, skip_browser_open=...)`.
PerformOAuthFlow = Callable[..., Awaitable[None]]


def get_config_url(config: McpServerConfig) -> str | None:
    """`getConfigUrl`: the server's URL, for the transports that have one."""
    return config.url


def _description(server_name: str, config: McpServerConfig) -> str:
    url = get_config_url(config)
    transport = config.type or "stdio"
    location = f"{transport} at {url}" if url else transport
    body = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1].strip()
    return render_prompt(body, {"SERVER_NAME": server_name, "LOCATION": location})


def _output(status: str, message: str, auth_url: str | None = None) -> ToolResult:
    """`McpAuthOutput` as the tool's data; the model reads its `message`."""
    data: dict[str, Any] = {"status": status, "message": message}
    if auth_url is not None:
        data["authUrl"] = auth_url
    return ToolResult(content=message, metadata=data)


def _allow(input: dict[str, Any], _context: Any) -> PermissionResult:
    """`checkPermissions`: always allowed."""
    return PermissionResult(behavior="allow", updated_input=input)


def create_mcp_auth_tool(
    server_name: str,
    config: McpServerConfig,
    *,
    reconnect: Reconnect,
    apply_reconnect: ApplyReconnect,
    background: set[asyncio.Task[None]],
    perform_oauth_flow: PerformOAuthFlow | None = None,
) -> McpBuiltTool:
    """
    `createMcpAuthTool`: the `mcp__<server>__authenticate` pseudo-tool of a `needs-auth` server.
    `perform_oauth_flow` replaces `perform_mcp_oauth_flow`, which is otherwise looked up per call.
    """
    transport = config.type or "stdio"
    description = _description(server_name, config)
    user_facing = f"{server_name} - authenticate (MCP)"

    async def continue_in_background(oauth: asyncio.Future[None]) -> None:
        """Once OAuth completes: clear the needs-auth cache, reconnect, swap the real tools in."""
        try:
            await oauth
            clear_mcp_auth_cache()
            result = await reconnect(server_name, config)
            apply_reconnect(server_name, result)
            logger.debug("MCP server %s: OAuth complete, reconnected with %d tool(s)", server_name, len(result.tools))
        except Exception as exc:  # noqa: BLE001 - upstream logs the failed background flow
            logger.error("MCP server %s: OAuth flow failed after tool-triggered start: %s", server_name, exc)

    async def call(_input: dict[str, Any], _context: Any) -> ToolResult:
        if config.type == "claudeai-proxy":
            return _output("unsupported", f"This is a claude.ai MCP connector. Ask the user to run /mcp and select "
                                          f'"{server_name}" to authenticate.')
        if config.type not in ("sse", "http"):
            return _output("unsupported", f'Server "{server_name}" uses {transport} transport which does not support '
                                          "OAuth from this tool. Ask the user to run /mcp and authenticate manually.")
        loop = asyncio.get_running_loop()
        auth_url: asyncio.Future[str | None] = loop.create_future()

        def on_authorization_url(url: str) -> None:
            if not auth_url.done():
                auth_url.set_result(url)

        controller = AbortController()
        flow = perform_oauth_flow if perform_oauth_flow is not None else perform_mcp_oauth_flow
        oauth: asyncio.Future[None] = asyncio.ensure_future(flow(
            server_name, config, on_authorization_url, controller.signal, skip_browser_open=True))

        def on_flow_done(done: asyncio.Future[None]) -> None:
            """`Promise.race([authUrlPromise, oauthPromise.then(() => null)])`'s second arm."""
            if auth_url.done():
                return
            if done.cancelled():
                auth_url.cancel()
            elif done.exception() is not None:
                auth_url.set_exception(done.exception())  # type: ignore[arg-type]
            else:
                auth_url.set_result(None)

        oauth.add_done_callback(on_flow_done)
        task = loop.create_task(continue_in_background(oauth))
        background.add(task)
        task.add_done_callback(background.discard)
        try:
            url = await asyncio.shield(auth_url)
        except Exception as exc:  # noqa: BLE001 - upstream turns the failed start into an error result
            return _output("error", f"Failed to start OAuth flow for {server_name}: {exc}. Ask the user to run /mcp "
                                    "and authenticate manually.")
        if url:
            return _output("auth_url", f"Ask the user to open this URL in their browser to authorize the {server_name} "
                                       f"MCP server:\n\n{url}\n\nOnce they complete the flow, the server's tools will "
                                       "become available automatically.", url)
        return _output("auth_url", f"Authentication completed silently for {server_name}. The server's tools should "
                                   "now be available.")

    built = build_tool(
        name=build_mcp_tool_name(server_name, MCP_AUTH_TOOL_NAME),
        prompt=description,
        call=call,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        description=lambda _args: description,
        is_enabled=lambda: True,
        is_concurrency_safe=lambda _args: False,
        is_read_only=lambda _args: False,
        check_permissions=_allow,
        is_mcp=True,
        max_result_size_chars=MCP_AUTH_MAX_RESULT_SIZE_CHARS,
        user_facing_name=lambda _args: user_facing,
        render_tool_use_message=lambda _input, _verbose: f"Authenticate {server_name} MCP server",
    )
    return McpBuiltTool(
        **{f: getattr(built, f) for f in BuiltTool.__dataclass_fields__},
        server_name=server_name,
        mcp_tool_name=MCP_AUTH_TOOL_NAME,
        _auto_classifier_input=lambda _args: server_name,
    )


__all__ = [
    "MCP_AUTH_MAX_RESULT_SIZE_CHARS",
    "MCP_AUTH_TOOL_NAME",
    "PerformOAuthFlow",
    "create_mcp_auth_tool",
    "get_config_url",
]
