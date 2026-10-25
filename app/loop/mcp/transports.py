"""
The transport each MCP server type connects over (Phase 8).

Ported from `reference/claude-code/src/services/mcp/client.ts` -> `connectToServer`'s transport
branch (`StdioClientTransport`, `SSEClientTransport`, `StreamableHTTPClientTransport`,
`WebSocketTransport`), `utils/http.ts` -> `getMCPUserAgent`, and
`services/mcp/headersHelper.ts` -> `getMcpHeadersFromHelper` / `getMcpServerHeaders`.

A transport is an async context manager yielding the SDK's `(read, write)` streams. The
connection layer takes the factory as a parameter, which is the seam the offline tests use
to put an in-memory server where a process or a socket would be.

* **stdio** gets the full process environment plus the entry's `env`, as upstream's
  `subprocessEnv()` spread. The SDK's own default is a minimal allow-list, so it is
  overridden on purpose. The server's stderr goes to a temporary file (upstream pipes it so it
  never reaches the UI) and is logged when the connection fails.
* **sse** / **http** send `User-Agent` plus the static headers, overridden by the
  `headersHelper` output. A TDDAgents run is always non-interactive, so — as upstream's
  `getIsNonInteractiveSession()` branch — the helper runs without a trust prompt.
* **ws** follows the 1.x SDK's `websocket_client` (subprotocol `mcp`, one JSON-RPC message per
  text frame): `mcp` 2.x ships no WebSocket client, and upstream supports the type.

Not ported: OAuth (`ClaudeAuthProvider`), the session-ingress token, proxy and mTLS options,
`CLAUDE_CODE_SHELL_PREFIX`, and the IDE / claude.ai / in-process transports.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import cache
from pathlib import Path
from typing import Any

import anyio
from mcp.client._transport import TransportStreams
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.message import SessionMessage
from mcp.types import jsonrpc_message_adapter

from app.loop.mcp.types import McpServerConfig

logger = logging.getLogger(__name__)

#: `headersHelper.ts`: the helper's `execFileNoThrowWithCwd` timeout.
HEADERS_HELPER_TIMEOUT_SECONDS = 10.0

#: `(server name, config) -> transport`.
TransportFactory = Callable[[str, McpServerConfig], AbstractAsyncContextManager[TransportStreams]]

_APP_ROOT = Path(__file__).resolve().parents[3]


@cache
def client_version() -> str:
    """`MACRO.VERSION`: this checkout's short git sha, as the transcript stamp uses."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], check=False, cwd=_APP_ROOT, capture_output=True, text=True
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


def get_mcp_user_agent() -> str:
    """`getMCPUserAgent` (no entrypoint or SDK suffixes: neither exists here)."""
    return f"tddagents/{client_version()}"


def get_mcp_headers_from_helper(server_name: str, config: McpServerConfig) -> dict[str, str] | None:
    """`getMcpHeadersFromHelper`: the helper's JSON object of headers, or `None` on any failure."""
    if not config.headers_helper:
        return None
    env = {**os.environ, "TDDAGENTS_MCP_SERVER_NAME": server_name, "TDDAGENTS_MCP_SERVER_URL": config.url or ""}
    try:
        done = subprocess.run(
            config.headers_helper, shell=True, check=False, capture_output=True, text=True,
            timeout=HEADERS_HELPER_TIMEOUT_SECONDS, env=env,
        )
        if done.returncode != 0 or not done.stdout:
            raise ValueError(f"headersHelper for MCP server '{server_name}' did not return a valid value")
        headers = json.loads(done.stdout.strip())
        if not isinstance(headers, dict):
            raise TypeError(
                f"headersHelper for MCP server '{server_name}' must return a JSON object with string key-value pairs"
            )
        for key, value in headers.items():
            if not isinstance(value, str):
                raise TypeError(
                    f"headersHelper for MCP server '{server_name}' returned non-string value for key "
                    f'"{key}": {type(value).__name__}'
                )
        return dict(headers)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        logger.warning("MCP server %s: error getting headers from headersHelper: %s", server_name, exc)
        return None


def get_mcp_server_headers(server_name: str, config: McpServerConfig) -> dict[str, str]:
    """`getMcpServerHeaders`: static headers, overridden by the helper's."""
    return {**(config.headers or {}), **(get_mcp_headers_from_helper(server_name, config) or {})}


@asynccontextmanager
async def _stdio(name: str, config: McpServerConfig) -> AsyncIterator[TransportStreams]:
    params = StdioServerParameters(
        command=str(config.command), args=list(config.args), env={**os.environ, **(config.env or {})}
    )
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
        try:
            async with stdio_client(params, errlog=errlog) as streams:
                yield streams
        except Exception:
            errlog.seek(0)
            stderr = errlog.read()
            if stderr:
                logger.warning("MCP server %s stderr: %s", name, stderr)
            raise


@asynccontextmanager
async def _http(name: str, config: McpServerConfig) -> AsyncIterator[TransportStreams]:
    headers = {"User-Agent": get_mcp_user_agent(), **get_mcp_server_headers(name, config)}
    async with (
        create_mcp_http_client(headers=headers) as http_client,
        streamable_http_client(str(config.url), http_client=http_client) as streams,
    ):
        yield streams


@asynccontextmanager
async def _sse(name: str, config: McpServerConfig) -> AsyncIterator[TransportStreams]:
    headers = {"User-Agent": get_mcp_user_agent(), **get_mcp_server_headers(name, config)}
    async with sse_client(str(config.url), headers=headers) as streams:
        yield streams


@asynccontextmanager
async def websocket_client(url: str, headers: Mapping[str, str]) -> AsyncIterator[TransportStreams]:
    """The 1.x SDK's `websocket_client`: subprotocol `mcp`, one JSON-RPC message per text frame."""
    from websockets.asyncio.client import connect
    from websockets.typing import Subprotocol

    read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)
    async with connect(url, subprotocols=[Subprotocol("mcp")], additional_headers=dict(headers)) as ws:

        async def ws_reader() -> None:
            async with read_writer:
                async for raw in ws:
                    try:
                        message = jsonrpc_message_adapter.validate_json(raw, by_name=False)
                    except ValueError as exc:
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))

        async def ws_writer() -> None:
            async with write_reader:
                async for session_message in write_reader:
                    await ws.send(session_message.message.model_dump_json(by_alias=True, exclude_unset=True))

        async with anyio.create_task_group() as tg:
            tg.start_soon(ws_reader)
            tg.start_soon(ws_writer)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()


@asynccontextmanager
async def _ws(name: str, config: McpServerConfig) -> AsyncIterator[TransportStreams]:
    headers = {"User-Agent": get_mcp_user_agent(), **get_mcp_server_headers(name, config)}
    async with websocket_client(str(config.url), headers) as streams:
        yield streams


_TRANSPORTS: dict[str, Callable[[str, McpServerConfig], AbstractAsyncContextManager[Any]]] = {
    "stdio": _stdio,
    "sse": _sse,
    "http": _http,
    "ws": _ws,
}


def open_transport(name: str, config: McpServerConfig) -> AbstractAsyncContextManager[TransportStreams]:
    """The production `TransportFactory`; an unsupported type raises as upstream's `else` does."""
    opener = _TRANSPORTS.get(config.transport)
    if opener is None:
        raise ValueError(f"Unsupported server type: {config.transport}")
    return opener(name, config)


__all__ = [
    "HEADERS_HELPER_TIMEOUT_SECONDS",
    "TransportFactory",
    "client_version",
    "get_mcp_headers_from_helper",
    "get_mcp_server_headers",
    "get_mcp_user_agent",
    "open_transport",
    "websocket_client",
]
