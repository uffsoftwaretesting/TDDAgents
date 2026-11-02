"""
SDK MCP servers: MCP servers that live in the SDK host, reached through control requests.

Ported from:
- `reference/claude-code/src/services/mcp/SdkControlTransport.ts` -> `SdkControlClientTransport`: each
  message the client sends goes to the host as an `mcp_message` control request, and the host's
  answer is delivered back as the server's message; a failed `sendMcpMessage` fails the client's
  pending request (upstream's `send()` rejects, so `Protocol.request` rejects),
- `reference/claude-code/src/services/mcp/client.ts` -> `setupSdkMcpClients` (kept here, beside its
  transport): every SDK server connected in parallel, its tools fetched, a failure recorded as a
  failed server.

The Python `mcp` client reads and writes anyio streams, so the transport is a pair of memory streams
with a pump between them. A notification the client sends gets no answer back (upstream hands the
host's reply to `onmessage` whatever it is; a reply to a notification has no request to settle). A
message the host sends on its own (`mcp_message` inbound) is injected with `deliver`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp.types as mcp_types
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

#: `SendMcpMessageCallback`: (server name, JSON-RPC message) -> the server's answer.
SendMcpMessage = Callable[[str, dict[str, Any]], Awaitable[Any]]


class SdkControlClientTransport:
    """`SdkControlClientTransport`: the client side of an SDK server, over the control protocol."""

    def __init__(self, server_name: str, send_mcp_message: SendMcpMessage) -> None:
        self.server_name = server_name
        self.send_mcp_message = send_mcp_message
        self.is_closed = False
        self._inbound: Any = None

    async def deliver(self, message: Mapping[str, Any]) -> None:
        """The host's own message for this client (`transport.onmessage`)."""
        if self._inbound is not None and not self.is_closed:
            await self._inbound.send(SessionMessage(mcp_types.jsonrpc_message_adapter.validate_python(dict(message))))

    @asynccontextmanager
    async def streams(self) -> AsyncIterator[tuple[Any, Any]]:
        """The `(read, write)` streams a `ClientSession` runs over."""
        read_send, read_receive = anyio.create_memory_object_stream[SessionMessage | Exception](16)
        write_send, write_receive = anyio.create_memory_object_stream[SessionMessage](16)
        self._inbound = read_send

        async def pump() -> None:
            async with write_receive:
                async for outgoing in write_receive:
                    message = outgoing.message
                    payload = message.model_dump(by_alias=True, mode="json", exclude_unset=True)
                    try:
                        answer = await self.send_mcp_message(self.server_name, payload)
                    except Exception as error:  # noqa: BLE001 - the pending request fails, as `send()` rejects
                        if isinstance(message, mcp_types.JSONRPCRequest):
                            await read_send.send(SessionMessage(mcp_types.jsonrpc_message_adapter.validate_python({
                                "jsonrpc": "2.0", "id": message.id,
                                "error": {"code": mcp_types.INTERNAL_ERROR, "message": str(error)}})))
                        else:
                            logger.error("MCP server %s: SDK message failed: %s", self.server_name, error)
                        continue
                    if isinstance(message, mcp_types.JSONRPCRequest) and isinstance(answer, Mapping):
                        await read_send.send(SessionMessage(mcp_types.jsonrpc_message_adapter.validate_python(
                            dict(answer))))

        async with anyio.create_task_group() as group:
            group.start_soon(pump)
            try:
                yield read_receive, write_send
            finally:
                self.is_closed = True
                self._inbound = None
                group.cancel_scope.cancel()
                await read_send.aclose()


async def setup_sdk_mcp_clients(
    configs: Mapping[str, Any], send_mcp_message: SendMcpMessage, *, cwd: str,
    connect: Callable[..., Awaitable[Any]] | None = None,
) -> tuple[list[Any], list[Any], dict[str, SdkControlClientTransport]]:
    """
    `setupSdkMcpClients`: each SDK server connected over its control transport; returns the clients,
    their tools and the transports (for inbound `mcp_message`).
    """
    import asyncio
    import dataclasses

    from app.services.mcp.client import connect_to_server, fetch_tools_for_client
    from app.services.mcp.types import ConnectedMCPServer, FailedMCPServer
    from app.tools.mcp_tool.mcp_tool import build_mcp_tool

    connect = connect or connect_to_server
    transports: dict[str, SdkControlClientTransport] = {}

    async def one(name: str, config: Any) -> tuple[Any, list[Any]]:
        transport = SdkControlClientTransport(name, send_mcp_message)
        transports[name] = transport
        scoped = dataclasses.replace(config, scope="dynamic")
        try:
            client = await connect(name, scoped, cwd=cwd, transport_factory=lambda _n, _c: transport.streams())
            if not isinstance(client, ConnectedMCPServer):
                return client, []
            tools = []
            if getattr(client.capabilities, "tools", None):
                tools = [build_mcp_tool(client, d) for d in await fetch_tools_for_client(client)]
            return client, tools
        except Exception as error:  # noqa: BLE001 - upstream records a failed SDK server
            logger.error("MCP server %s: Failed to connect SDK MCP server: %s", name, error)
            return FailedMCPServer(name=name, config=dataclasses.replace(config, scope="user")), []

    results = await asyncio.gather(*(one(name, config) for name, config in configs.items()))
    return [client for client, _ in results], [tool for _, tools in results for tool in tools], transports


__all__ = ["SdkControlClientTransport", "SendMcpMessage", "setup_sdk_mcp_clients"]
