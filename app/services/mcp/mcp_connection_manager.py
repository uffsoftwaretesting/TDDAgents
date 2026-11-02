"""
A run's MCP connections (Phase 8).

Ported from `reference/claude-code/src/services/mcp/client.ts` ->
`getMcpToolsCommandsAndResources` (with `processBatched`, `isLocalMcpServer`,
`getMcpServerConnectionBatchSize`, `getRemoteMcpServerConnectionBatchSize`,
`isIncludedMcpTool`), `reconnectMcpServerImpl`, and the session-end `clearServerCache` cleanup.

`McpConnectionManager.connect` takes the run's configs (`config.get_claude_code_mcp_configs`)
and connects local (stdio) servers three at a time and remote ones twenty at a time
(`TDDAGENTS_MCP_SERVER_CONNECTION_BATCH_SIZE` / `TDDAGENTS_MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE`,
upstream's `MCP_SERVER_CONNECTION_BATCH_SIZE` / `MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE`). A
disabled server is recorded as `disabled`, a server that fails as `failed`; neither stops the
others. The resource tools join the pool once, with the first server that supports resources.

`clients` is the `mcp_clients` sequence the context carries (upstream `options.mcpClients`):
`utils/attachments.py` and the resource tools read each entry's `type`, `name` and
`instructions`. `aclose` closes every connected session; the runner calls it when the run ends.

An sse/http server that answered 401 within the last 15 minutes (`is_mcp_auth_cached`), or
that was probed before but holds no token (`has_mcp_discovery_but_no_token`), is recorded as
`needs-auth` without a connection attempt, as upstream's `processServer` does. A `needs-auth`
server, skipped or answering 401, gets `createMcpAuthTool`'s `mcp__<server>__authenticate` in
`tools` instead of its own (`tools/mcp_auth_tool/`). When that tool's OAuth flow succeeds, the
manager reconnects the server (`reconnect`) and applies the result (`apply_reconnect`, the
`setAppState` update upstream's tool makes): the server's entry in `clients` is replaced, and
every `mcp__<server>__` tool in `tools` (the auth tool among them) gives way to the reconnected
server's tools, both lists updated in place. Then `on_reconnect(server_name, result)`, when the
run set one, lets the run refresh what it derived from them (its tool pool, its context's
`mcp_clients`). `aclose` cancels an auth flow still running in the background.

`elicitation_callback` and `on_elicitation_complete` (see `elicitation_handler.py`) are passed to
every connection the manager makes, the first ones and the reconnections alike.

`reconnect_mcp_server_impl` is `client.ts` -> `reconnectMcpServerImpl` (kept here, beside the
tool building it shares, because `client.py` cannot import the tool modules that import it):
connect again, then the server's tools and resources.

Not ported: MCP prompts as commands and MCP skills, plugin servers, `list_changed`
notifications, and the per-server analytics.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.mcp.auth import has_mcp_discovery_but_no_token
from app.services.mcp.client import (
    TransportFactory,
    connect_to_server,
    fetch_resources_for_client,
    fetch_tools_for_client,
    is_mcp_auth_cached,
    open_transport,
    parse_int_env,
)
from app.services.mcp.config import is_mcp_server_disabled
from app.services.mcp.elicitation_handler import ElicitationCallback, OnElicitationComplete
from app.services.mcp.normalization import get_mcp_prefix
from app.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpServerConfig,
    MCPServerConnection,
    NeedsAuthMCPServer,
    ServerResource,
)
from app.tools.list_mcp_resources_tool.list_mcp_resources_tool import (
    build_list_mcp_resources_tool,
)
from app.tools.mcp_auth_tool.mcp_auth_tool import create_mcp_auth_tool
from app.tools.mcp_tool.mcp_tool import build_mcp_tool
from app.tools.read_mcp_resource_tool.read_mcp_resource_tool import (
    build_read_mcp_resource_tool,
)
from app.utils.secure_storage.index import clear_keychain_cache

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
    #: The connections' elicitation callback (`None`: every elicitation is answered `cancel`).
    elicitation_callback: ElicitationCallback | None = None
    #: The connections' `notifications/elicitation/complete` handler.
    on_elicitation_complete: OnElicitationComplete | None = None
    #: Told after an auth tool's reconnect is applied: `(server_name, result)`.
    on_reconnect: Callable[[str, ReconnectResult], None] | None = None
    _resource_tools_added: bool = False
    #: The auth tools' background OAuth continuations.
    _auth_flows: set[asyncio.Task[None]] = field(default_factory=set)

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
        if config.type in ("sse", "http") and (
            is_mcp_auth_cached(name) or has_mcp_discovery_but_no_token(name, config)
        ):
            logger.debug("MCP server %s: Skipping connection (cached needs-auth)", name)
            self.clients.append(NeedsAuthMCPServer(name=name, config=config))
            self.tools.append(self._auth_tool(name, config))
            return
        try:
            client = await connect_to_server(
                name, config, cwd=self.cwd, transport_factory=self.transport_factory,
                elicitation_callback=self.elicitation_callback, on_elicitation_complete=self.on_elicitation_complete)
            self.clients.append(client)
            if not isinstance(client, ConnectedMCPServer):
                if isinstance(client, NeedsAuthMCPServer):
                    self.tools.append(self._auth_tool(name, config))
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

    def _auth_tool(self, name: str, config: McpServerConfig) -> Any:
        """`createMcpAuthTool(name, config)`, reconnecting through this manager."""
        return create_mcp_auth_tool(name, config, reconnect=self.reconnect, apply_reconnect=self.apply_reconnect,
                                    background=self._auth_flows)

    async def reconnect(self, name: str, config: McpServerConfig) -> ReconnectResult:
        """`reconnectMcpServerImpl(name, config)` with this manager's cwd, transport and elicitation."""
        return await reconnect_mcp_server_impl(
            name, config, cwd=self.cwd, transport_factory=self.transport_factory,
            elicitation_callback=self.elicitation_callback, on_elicitation_complete=self.on_elicitation_complete)

    def apply_reconnect(self, name: str, result: ReconnectResult) -> None:
        """
        The auth tool's `setAppState` update: the server's client replaced, its `mcp__<server>__`
        tools replaced by the reconnected ones, then `on_reconnect`.
        """
        self.clients[:] = [result.client if c.name == name else c for c in self.clients]
        prefix = get_mcp_prefix(name)
        self.tools[:] = [*(t for t in self.tools if not str(getattr(t, "name", "")).startswith(prefix)), *result.tools]
        if self.on_reconnect is not None:
            self.on_reconnect(name, result)

    async def aclose(self) -> None:
        """Close every connected session (upstream's cleanup at session end)."""
        for flow in list(self._auth_flows):
            flow.cancel()
        await asyncio.gather(*list(self._auth_flows), return_exceptions=True)
        cleanups = [c.cleanup for c in self.clients if isinstance(c, ConnectedMCPServer) and c.cleanup]
        await asyncio.gather(*(cleanup() for cleanup in cleanups), return_exceptions=True)


@dataclass
class ReconnectResult:
    """`reconnectMcpServerImpl`'s `{ client, tools, commands, resources? }` (no MCP commands here)."""

    client: MCPServerConnection
    tools: list[Any] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    resources: list[ServerResource] | None = None


async def reconnect_mcp_server_impl(
    name: str,
    config: McpServerConfig,
    *,
    cwd: str | Path,
    transport_factory: TransportFactory = open_transport,
    elicitation_callback: ElicitationCallback | None = None,
    on_elicitation_complete: OnElicitationComplete | None = None,
) -> ReconnectResult:
    """`reconnectMcpServerImpl`: a fresh connection; its tools and resources when connected."""
    try:
        clear_keychain_cache()
        client = await connect_to_server(
            name, config, cwd=cwd, transport_factory=transport_factory, elicitation_callback=elicitation_callback,
            on_elicitation_complete=on_elicitation_complete)
        if not isinstance(client, ConnectedMCPServer):
            return ReconnectResult(client=client)
        supports_resources = bool(getattr(client.capabilities, "resources", None))
        definitions = await fetch_tools_for_client(client)
        tools: list[Any] = [t for t in (build_mcp_tool(client, d) for d in definitions) if is_included_mcp_tool(t.name)]
        resources = await fetch_resources_for_client(client) if supports_resources else []
        if supports_resources:
            tools.extend((build_list_mcp_resources_tool(), build_read_mcp_resource_tool()))
        return ReconnectResult(client=client, tools=tools, resources=resources or None)
    except Exception as exc:  # noqa: BLE001 - upstream answers a failed client
        logger.warning("MCP server %s: error during reconnection: %s", name, exc)
        return ReconnectResult(client=FailedMCPServer(name=name, config=config))


__all__ = [
    "ALLOWED_IDE_TOOLS",
    "DEFAULT_LOCAL_BATCH_SIZE",
    "DEFAULT_REMOTE_BATCH_SIZE",
    "LOCAL_BATCH_SIZE_ENV",
    "REMOTE_BATCH_SIZE_ENV",
    "McpConnectionManager",
    "ReconnectResult",
    "get_mcp_server_connection_batch_size",
    "get_remote_mcp_server_connection_batch_size",
    "is_included_mcp_tool",
    "is_local_mcp_server",
    "process_batched",
    "reconnect_mcp_server_impl",
]
