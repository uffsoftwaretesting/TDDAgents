"""
A run's MCP connections (Phase 8).

Ported from `reference/claude-code/src/services/mcp/client.ts` ->
`getMcpToolsCommandsAndResources` (with `processBatched`, `isLocalMcpServer`,
`getMcpServerConnectionBatchSize`, `getRemoteMcpServerConnectionBatchSize`,
`isIncludedMcpTool`) and the session-end `clearServerCache` cleanup.

`McpConnectionManager.connect` takes the run's configs (`config.get_claude_code_mcp_configs`)
and connects local (stdio) servers three at a time and remote ones twenty at a time
(`TDDAGENTS_MCP_SERVER_CONNECTION_BATCH_SIZE` / `TDDAGENTS_MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE`,
upstream's `MCP_SERVER_CONNECTION_BATCH_SIZE` / `MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE`). A
disabled server is recorded as `disabled`, a server that fails as `failed`; neither stops the
others. The resource tools join the pool once, with the first server that supports resources.

`clients` is the `mcp_clients` sequence the context carries (upstream `options.mcpClients`):
`attachments/deltas.py` and the resource tools read each entry's `type`, `name` and
`instructions`. `aclose` closes every connected session; the runner calls it when the run ends.

Not ported: `needs-auth` and the auth tool, MCP prompts as commands and MCP skills, plugin
servers, `list_changed` notifications and reconnection, and the per-server analytics.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.loop.mcp.client import connect_to_server, fetch_tools_for_client, parse_int_env
from app.loop.mcp.config import is_mcp_server_disabled
from app.loop.mcp.tools import (
    build_list_mcp_resources_tool,
    build_mcp_tool,
    build_read_mcp_resource_tool,
)
from app.loop.mcp.transports import TransportFactory, open_transport
from app.loop.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpServerConfig,
    MCPServerConnection,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_BATCH_SIZE = 3
DEFAULT_REMOTE_BATCH_SIZE = 20
LOCAL_BATCH_SIZE_ENV = "TDDAGENTS_MCP_SERVER_CONNECTION_BATCH_SIZE"
REMOTE_BATCH_SIZE_ENV = "TDDAGENTS_MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE"
#: `ALLOWED_IDE_TOOLS`.
ALLOWED_IDE_TOOLS = frozenset({"mcp__ide__executeCode", "mcp__ide__getDiagnostics"})


def get_mcp_server_connection_batch_size() -> int:
    """`getMcpServerConnectionBatchSize`."""
    return parse_int_env(LOCAL_BATCH_SIZE_ENV, DEFAULT_LOCAL_BATCH_SIZE)


def get_remote_mcp_server_connection_batch_size() -> int:
    """`getRemoteMcpServerConnectionBatchSize`."""
    return parse_int_env(REMOTE_BATCH_SIZE_ENV, DEFAULT_REMOTE_BATCH_SIZE)


def is_local_mcp_server(config: McpServerConfig) -> bool:
    """`isLocalMcpServer`."""
    return config.type in (None, "stdio", "sdk")


def is_included_mcp_tool(name: str) -> bool:
    """`isIncludedMcpTool`: of the IDE server, only its two allowed tools."""
    return not name.startswith("mcp__ide__") or name in ALLOWED_IDE_TOOLS


async def process_batched(
    items: Iterable[Any], concurrency: int, processor: Callable[[Any], Awaitable[None]]
) -> None:
    """`processBatched` (`pMap` with `{ concurrency }`)."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run(item: Any) -> None:
        async with semaphore:
            await processor(item)

    await asyncio.gather(*(run(item) for item in items))


@dataclass
class McpConnectionManager:
    """The run's MCP servers: their connection states, their tools, and their shutdown."""

    cwd: str | Path
    transport_factory: TransportFactory = open_transport
    clients: list[MCPServerConnection] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    _resource_tools_added: bool = False

    async def connect(self, configs: Mapping[str, McpServerConfig]) -> None:
        """`getMcpToolsCommandsAndResources`: connect every server and collect its tools."""
        entries: list[tuple[str, McpServerConfig]] = []
        for name, config in configs.items():
            if is_mcp_server_disabled(name, self.cwd):
                self.clients.append(DisabledMCPServer(name=name, config=config))
            else:
                entries.append((name, config))
        local = [e for e in entries if is_local_mcp_server(e[1])]
        remote = [e for e in entries if not is_local_mcp_server(e[1])]
        await asyncio.gather(
            process_batched(local, get_mcp_server_connection_batch_size(), self._process_server),
            process_batched(remote, get_remote_mcp_server_connection_batch_size(), self._process_server),
        )

    async def _process_server(self, entry: tuple[str, McpServerConfig]) -> None:
        name, config = entry
        try:
            client = await connect_to_server(name, config, cwd=self.cwd, transport_factory=self.transport_factory)
            self.clients.append(client)
            if not isinstance(client, ConnectedMCPServer):
                return
            definitions = await fetch_tools_for_client(client)
            built = [build_mcp_tool(client, d) for d in definitions]
            self.tools.extend(t for t in built if is_included_mcp_tool(t.name))
            if getattr(client.capabilities, "resources", None) and not self._resource_tools_added:
                self._resource_tools_added = True
                self.tools.extend((build_list_mcp_resources_tool(), build_read_mcp_resource_tool()))
        except Exception as exc:  # noqa: BLE001 - upstream records the server as failed
            logger.warning("MCP server %s: error fetching tools/resources: %s", name, exc)
            self.clients.append(FailedMCPServer(name=name, config=config, error=str(exc)))

    async def aclose(self) -> None:
        """Close every connected session (upstream's cleanup at session end)."""
        cleanups = [c.cleanup for c in self.clients if isinstance(c, ConnectedMCPServer) and c.cleanup]
        await asyncio.gather(*(cleanup() for cleanup in cleanups), return_exceptions=True)


__all__ = [
    "ALLOWED_IDE_TOOLS",
    "DEFAULT_LOCAL_BATCH_SIZE",
    "DEFAULT_REMOTE_BATCH_SIZE",
    "LOCAL_BATCH_SIZE_ENV",
    "REMOTE_BATCH_SIZE_ENV",
    "McpConnectionManager",
    "get_mcp_server_connection_batch_size",
    "get_remote_mcp_server_connection_batch_size",
    "is_included_mcp_tool",
    "is_local_mcp_server",
    "process_batched",
]
