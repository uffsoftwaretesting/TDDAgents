"""
A session's MCP servers while it runs: the configured ones, the SDK host's and the dynamic ones.

Ported from `reference/claude-code/src/cli/print.ts`'s closure state and the functions over it:
`sdkMcpConfigs` / `sdkClients` / `sdkTools`, `dynamicMcpState`, `updateSdkMcp`,
`applyMcpServerChanges` (serialized), `handleMcpSetServers`, `reconcileMcpServers`, `toScopedConfig`,
`buildMcpServerStatuses`, and the `mcp_reconnect` / `mcp_toggle` bodies (`reconnectMcpServerImpl`, then
`appState.mcp` and `dynamicMcpState` updated). Upstream keeps it in `runHeadlessStreaming`'s closure;
here it is one object on the run (`DevelopmentRun.mcp`), so the tool pools read it live: every
change calls `on_change`, which rebuilds the rosters and the pool (upstream's `buildAllTools`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.mcp.config import set_mcp_server_enabled
from app.services.mcp.mcp_connection_manager import ReconnectResult, reconnect_mcp_server_impl
from app.services.mcp.sdk_control_transport import SdkControlClientTransport, SendMcpMessage, setup_sdk_mcp_clients
from app.services.mcp.types import ConnectedMCPServer, DisabledMCPServer, McpServerConfig

logger = logging.getLogger(__name__)

#: `filterMcpServersByPolicy`'s reason for a blocked server.
BLOCKED_BY_POLICY = "Blocked by enterprise policy (allowedMcpServers/deniedMcpServers)"


def mcp_prefix(server_name: str) -> str:
    """`getMcpPrefix`."""
    from app.services.mcp.normalization import normalize_name_for_mcp

    return f"mcp__{normalize_name_for_mcp(server_name)}__"


def to_scoped_config(raw: Mapping[str, Any]) -> McpServerConfig:
    """`toScopedConfig`: a host-given server entry, scoped `dynamic` (an `sdk` entry kept as is)."""
    from app.services.mcp.types import server_config_from_raw

    if raw.get("type") == "sdk":
        return McpServerConfig(type="sdk", scope="dynamic")
    return server_config_from_raw(dict(raw), "dynamic")


@dataclass
class SessionMcp:
    """The run's MCP state and the operations the control requests run on it."""

    #: The configured servers (`appState.mcp`): their connections and tools.
    manager: Any
    cwd: str
    on_change: Callable[[], None] = lambda: None
    #: The host's `sendMcpMessage`, once a stream-json session has one.
    send_mcp_message: SendMcpMessage | None = None
    #: The run's settings locations, read when `mcp_set_servers` applies the enterprise policy.
    settings_locations: Callable[[], Any] | None = None
    #: `connect_to_server` / `reconnect_mcp_server_impl` keyword arguments (e.g. the elicitation callback).
    connect_options: dict[str, Any] = field(default_factory=dict)
    #: The run's hook dispatcher, for the Elicitation / ElicitationResult / Notification hooks.
    hook_dispatcher: Any = None
    #: The host's `handleElicitation`, once a session has one; without it an elicitation is cancelled.
    ask_host: Any = None
    #: Where `system/elicitation_complete` goes (the SDK output).
    emit_elicitation_complete: Callable[[str, str], None] | None = None
    sdk_configs: dict[str, McpServerConfig] = field(default_factory=dict)
    sdk_clients: list[Any] = field(default_factory=list)
    sdk_tools: list[Any] = field(default_factory=list)
    sdk_transports: dict[str, SdkControlClientTransport] = field(default_factory=dict)
    dynamic_configs: dict[str, McpServerConfig] = field(default_factory=dict)
    dynamic_clients: list[Any] = field(default_factory=list)
    dynamic_tools: list[Any] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── views ────────────────────────────────────────────────────────────────

    def all_clients(self) -> list[Any]:
        """Every connection: the configured, the SDK and the dynamic servers."""
        return [*self.manager.clients, *self.sdk_clients, *self.dynamic_clients]

    def all_tools(self) -> list[Any]:
        """Every MCP tool, the first of each name kept (`uniqBy(..., 'name')`)."""
        seen: set[str] = set()
        tools = []
        for tool in [*self.manager.tools, *self.sdk_tools, *self.dynamic_tools]:
            if tool.name not in seen:
                seen.add(tool.name)
                tools.append(tool)
        return tools

    def find_config(self, name: str) -> McpServerConfig | None:
        """
        The config-existence gate of `mcp_reconnect` / `mcp_toggle`: the configured servers
        (`getMcpConfigByName`), then every client list.
        """
        from app.services.mcp.config import get_mcp_config_by_name

        if self.settings_locations is not None:
            config = get_mcp_config_by_name(name, self.cwd, self.settings_locations())
            if config is not None:
                return config
        client = next((c for c in self.all_clients() if c.name == name), None)
        return client.config if client is not None else None

    def statuses(self) -> list[dict[str, Any]]:
        """`buildMcpServerStatuses`."""
        from app.cli.print import build_mcp_server_statuses

        configured = [*self.manager.clients, *self.sdk_clients]
        names = {client.name for client in configured}
        clients = [*configured, *(c for c in self.dynamic_clients if c.name not in names)]
        # Upstream's `appState.mcp.tools` holds the SDK tools too (`updateSdkMcp`).
        return build_mcp_server_statuses(clients, self.all_tools())

    # ── elicitation ──────────────────────────────────────────────────────────
    # `registerElicitationHandlers`: upstream installs the handlers on each connected client before
    # every turn; here every connection gets these two from the start, and they read the hooks and
    # the host when an elicitation arrives, so a server connected before the host keeps working.

    def elicitation_options(self) -> dict[str, Any]:
        """The connection keyword arguments that route a server's elicitations through this session."""
        return {"elicitation_callback": self.elicit, "on_elicitation_complete": self.elicitation_complete}

    async def elicit(self, server_name: str, params: Any) -> Any:
        """An MCP server's `elicitation/create`: the Elicitation hooks, then the host, then the result hooks."""
        from app.services.mcp.elicitation_handler import build_elicitation_callback

        return await build_elicitation_callback(self.hook_dispatcher, self._ask_host)(server_name, params)

    async def _ask_host(self, *args: Any) -> Any:
        if self.ask_host is None:
            return {"action": "cancel"}
        return await self.ask_host(*args)

    async def elicitation_complete(self, server_name: str, elicitation_id: str) -> None:
        """`notifications/elicitation/complete`: the Notification hook, then the SDK message."""
        from app.services.mcp.elicitation_handler import build_elicitation_complete_handler

        await build_elicitation_complete_handler(self.hook_dispatcher, self.emit_elicitation_complete)(
            server_name, elicitation_id)

    # ── reconnect / toggle ───────────────────────────────────────────────────

    async def reconnect(self, name: str, config: McpServerConfig) -> ReconnectResult:
        """`reconnectMcpServerImpl`, the result put in place of the server's client and tools."""
        if config.type == "sdk":
            await self.update_sdk(force=True)
            client = next((c for c in self.sdk_clients if c.name == name), None)
            tools = [t for t in self.sdk_tools if t.name.startswith(mcp_prefix(name))]
            return ReconnectResult(client=client or DisabledMCPServer(name=name, config=config), tools=tools)
        result: ReconnectResult = await reconnect_mcp_server_impl(name, config, cwd=self.cwd, **self.connect_options)
        self.apply_reconnect(name, result)
        return result

    def apply_reconnect(self, name: str, result: ReconnectResult) -> None:
        """`setAppState(... mcp: clients/tools ...)` and `dynamicMcpState` for one server."""
        prefix = mcp_prefix(name)
        if name in self.dynamic_configs:
            self.dynamic_clients = [*(c for c in self.dynamic_clients if c.name != name), result.client]
            self.dynamic_tools = [*(t for t in self.dynamic_tools if not t.name.startswith(prefix)), *result.tools]
        else:
            self.manager.clients = [result.client if c.name == name else c for c in self.manager.clients]
            if not any(c.name == name for c in self.manager.clients):
                self.manager.clients.append(result.client)
            self.manager.tools = [*(t for t in self.manager.tools if not t.name.startswith(prefix)), *result.tools]
        self.on_change()

    async def disable(self, name: str, config: McpServerConfig) -> None:
        """`mcp_toggle` with `enabled: false`: persisted, disconnected, its tools gone."""
        set_mcp_server_enabled(name, False, self.cwd)
        client = next((c for c in self.all_clients() if c.name == name), None)
        if isinstance(client, ConnectedMCPServer) and client.cleanup is not None:
            await client.cleanup()
        prefix = mcp_prefix(name)
        disabled = DisabledMCPServer(name=name, config=config)
        self.manager.clients = [disabled if c.name == name else c for c in self.manager.clients]
        self.manager.tools = [t for t in self.manager.tools if not t.name.startswith(prefix)]
        self.dynamic_clients = [disabled if c.name == name else c for c in self.dynamic_clients]
        self.dynamic_tools = [t for t in self.dynamic_tools if not t.name.startswith(prefix)]
        self.on_change()

    async def enable(self, name: str, config: McpServerConfig) -> ReconnectResult:
        """`mcp_toggle` with `enabled: true`: persisted, then reconnected."""
        set_mcp_server_enabled(name, True, self.cwd)
        return await self.reconnect(name, config)

    # ── SDK servers ──────────────────────────────────────────────────────────

    def add_sdk_servers(self, names: Sequence[str]) -> None:
        """`initialize.sdkMcpServers`: placeholder configs, connected by the next `update_sdk`."""
        for name in names:
            self.sdk_configs[name] = McpServerConfig(type="sdk", scope="dynamic")

    async def update_sdk(self, force: bool = False) -> None:
        """`updateSdkMcp`: (re)connect the SDK servers when the set changed or one is pending or failed."""
        if self.send_mcp_message is None:
            return
        current, connected = set(self.sdk_configs), {c.name for c in self.sdk_clients}
        changed = force or current != connected or any(
            getattr(c, "type", None) in ("pending", "failed") for c in self.sdk_clients)
        if not changed:
            return
        for client in self.sdk_clients:
            if isinstance(client, ConnectedMCPServer) and client.cleanup is not None:
                await client.cleanup()
        self.sdk_clients, self.sdk_tools, self.sdk_transports = await setup_sdk_mcp_clients(
            self.sdk_configs, self.send_mcp_message, cwd=self.cwd)
        self.on_change()

    async def deliver(self, server_name: str, message: Mapping[str, Any]) -> None:
        """`mcp_message`: the host's message for one of its SDK servers."""
        transport = self.sdk_transports.get(server_name)
        if transport is not None:
            await transport.deliver(message)

    # ── mcp_set_servers ──────────────────────────────────────────────────────

    async def set_servers(self, servers: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
        """`applyMcpServerChanges` (serialized): `handleMcpSetServers`, then `reconcileMcpServers`."""
        async with self._lock:
            return await self._set_servers(servers)

    async def _set_servers(self, servers: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
        from app.services.mcp.config import filter_mcp_servers_by_policy

        scoped = {name: to_scoped_config(raw) for name, raw in servers.items()}
        if self.settings_locations is not None:
            allowed, blocked = filter_mcp_servers_by_policy(scoped, self.settings_locations())
        else:
            allowed, blocked = scoped, []
        errors: dict[str, str] = {name: BLOCKED_BY_POLICY for name in blocked}
        sdk = {n: c for n, c in allowed.items() if c.type == "sdk"}
        process = {n: c for n, c in allowed.items() if c.type != "sdk"}
        sdk_added, sdk_removed = [], []
        for name in list(self.sdk_configs):
            if name not in sdk:
                client = next((c for c in self.sdk_clients if c.name == name), None)
                if isinstance(client, ConnectedMCPServer) and client.cleanup is not None:
                    await client.cleanup()
                self.sdk_clients = [c for c in self.sdk_clients if c.name != name]
                self.sdk_tools = [t for t in self.sdk_tools if not t.name.startswith(mcp_prefix(name))]
                self.sdk_configs.pop(name)
                sdk_removed.append(name)
        for name, config in sdk.items():
            if name not in self.sdk_configs:
                self.sdk_configs[name] = config
                sdk_added.append(name)
        added, removed, process_errors = await self._reconcile(process)
        self.on_change()
        response = {"added": [*sdk_added, *added], "removed": [*sdk_removed, *removed],
                    "errors": {**errors, **process_errors}}
        return response, bool(sdk_added or sdk_removed)

    async def _reconcile(self, desired: Mapping[str, McpServerConfig]) -> tuple[list[str], list[str], dict[str, str]]:
        """`reconcileMcpServers`: dynamic servers removed, replaced (config changed) and added."""
        from app.services.mcp.client import connect_to_server, fetch_tools_for_client
        from app.tools.mcp_tool.mcp_tool import build_mcp_tool

        current = dict(self.dynamic_configs)
        to_remove = [n for n in current if n not in desired]
        to_add = [n for n in desired if n not in current]
        to_replace = [n for n in current if n in desired and current[n] != desired[n]]
        removed: list[str] = []
        added: list[str] = []
        errors: dict[str, str] = {}
        for name in [*to_remove, *to_replace]:
            client = next((c for c in self.dynamic_clients if c.name == name), None)
            if isinstance(client, ConnectedMCPServer) and client.cleanup is not None:
                try:
                    await client.cleanup()
                except Exception as error:  # noqa: BLE001 - upstream logs a failed cleanup
                    logger.error("MCP server %s: cleanup failed: %s", name, error)
            self.dynamic_tools = [t for t in self.dynamic_tools if not t.name.startswith(mcp_prefix(name))]
            self.dynamic_clients = [c for c in self.dynamic_clients if c.name != name]
            if name in to_remove:
                removed.append(name)
        for name in [*to_add, *to_replace]:
            config = desired[name]
            try:
                client = await connect_to_server(name, config, cwd=self.cwd, **self.connect_options)
                self.dynamic_clients.append(client)
                if isinstance(client, ConnectedMCPServer):
                    self.dynamic_tools.extend(build_mcp_tool(client, d) for d in await fetch_tools_for_client(client))
                elif getattr(client, "type", None) == "failed":
                    errors[name] = getattr(client, "error", None) or "Connection failed"
                added.append(name)
            except Exception as error:  # noqa: BLE001 - upstream records the error per server
                errors[name] = str(error)
        self.dynamic_configs = {name: dataclasses.replace(config) for name, config in desired.items()}
        return added, removed, errors

    async def aclose(self) -> None:
        """Close the SDK and dynamic connections (the configured ones are the manager's)."""
        for client in [*self.sdk_clients, *self.dynamic_clients]:
            if isinstance(client, ConnectedMCPServer) and client.cleanup is not None:
                try:
                    await client.cleanup()
                except Exception as error:  # noqa: BLE001 - shutdown goes on whatever one server does
                    logger.warning("MCP server %s: cleanup failed: %s", client.name, error)


__all__ = ["BLOCKED_BY_POLICY", "SessionMcp", "mcp_prefix", "to_scoped_config"]
