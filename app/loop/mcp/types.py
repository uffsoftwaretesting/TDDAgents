"""
MCP configuration and connection types (Phase 8).

Ported from `reference/claude-code/src/services/mcp/types.ts`: `McpServerConfigSchema` (the
union of stdio, sse, sse-ide, ws-ide, http, ws, sdk and claudeai-proxy), `ScopedMcpServerConfig`,
`ConfigScope`, and the connection states `ConnectedMCPServer` / `FailedMCPServer` /
`DisabledMCPServer`. zod's union is replaced by `adheres_to_server_schema`, which accepts and
rejects the same shapes: a stdio entry has a non-empty `command` (its `type` is optional, for
backwards compatibility), `args` defaults to `[]`, and every map is string-valued.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

ConfigScope = Literal["local", "user", "project", "dynamic", "enterprise", "claudeai", "managed"]

#: `TransportSchema` plus the two IDE transports and the claude.ai proxy the union accepts.
SERVER_TYPES = frozenset({"stdio", "sse", "sse-ide", "ws-ide", "http", "ws", "sdk", "claudeai-proxy"})


@dataclass(frozen=True)
class McpServerConfig:
    """`ScopedMcpServerConfig`: one server entry and the scope it was read from."""

    type: str | None
    scope: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    url: str | None = None
    headers: Mapping[str, str] | None = None
    headers_helper: str | None = None

    @property
    def transport(self) -> str:
        """The effective transport: an entry without `type` is stdio."""
        return self.type or "stdio"


@dataclass(frozen=True)
class ValidationError:
    """`ValidationError` with its `mcpErrorMetadata` flattened."""

    path: str
    message: str
    scope: str
    severity: Literal["fatal", "warning"]
    file: str | None = None
    suggestion: str | None = None
    server_name: str | None = None


def _string_map(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def adheres_to_server_schema(raw: Any) -> bool:
    """
    Whether `raw` adheres to upstream's server union (`McpServerConfigSchema().safeParse`).
    Only the verdict matters: a failure is always reported as `SCHEMA_ERROR`, as upstream's is.
    """
    if not isinstance(raw, dict):
        return False
    kind = raw.get("type")
    if kind is not None and kind not in SERVER_TYPES:
        return False
    if kind in (None, "stdio"):
        command = raw.get("command")
        args = raw.get("args", [])
        return (
            isinstance(command, str) and bool(command)
            and isinstance(args, list) and all(isinstance(a, str) for a in args)
            and ("env" not in raw or _string_map(raw["env"]))
        )
    if kind == "sdk":
        return isinstance(raw.get("name"), str)
    if not isinstance(raw.get("url"), str):
        return False
    if kind in ("sse", "http", "ws"):
        return ("headers" not in raw or _string_map(raw["headers"])) and (
            "headersHelper" not in raw or isinstance(raw["headersHelper"], str))
    required = "id" if kind == "claudeai-proxy" else "ideName"
    return isinstance(raw.get(required), str)


def server_config_from_raw(raw: Mapping[str, Any], scope: str) -> McpServerConfig:
    """A validated raw entry as a `McpServerConfig`."""
    env = raw.get("env")
    headers = raw.get("headers")
    return McpServerConfig(
        type=raw.get("type"),
        scope=scope,
        command=raw.get("command"),
        args=tuple(raw.get("args") or ()),
        env=dict(env) if isinstance(env, dict) else None,
        url=raw.get("url"),
        headers=dict(headers) if isinstance(headers, dict) else None,
        headers_helper=raw.get("headersHelper"),
    )


@dataclass(frozen=True)
class ServerResource:
    """`ServerResource`: an MCP resource tagged with the server it came from."""

    uri: str
    name: str
    server: str
    mime_type: str | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uri": self.uri, "name": self.name}
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.description:
            out["description"] = self.description
        out["server"] = self.server
        return out


@dataclass
class ConnectedMCPServer:
    """`ConnectedMCPServer`: `session` is the SDK `ClientSession`, `cleanup` closes it."""

    name: str
    config: McpServerConfig
    session: Any
    capabilities: Any
    instructions: str | None = None
    server_info: Any = None
    cleanup: Callable[[], Awaitable[None]] | None = None
    type: Literal["connected"] = "connected"


@dataclass
class FailedMCPServer:
    """`FailedMCPServer`."""

    name: str
    config: McpServerConfig
    error: str | None = None
    type: Literal["failed"] = "failed"


@dataclass
class DisabledMCPServer:
    """`DisabledMCPServer`."""

    name: str
    config: McpServerConfig
    type: Literal["disabled"] = "disabled"


MCPServerConnection = ConnectedMCPServer | FailedMCPServer | DisabledMCPServer

__all__ = [
    "SERVER_TYPES",
    "ConfigScope",
    "ConnectedMCPServer",
    "DisabledMCPServer",
    "FailedMCPServer",
    "MCPServerConnection",
    "McpServerConfig",
    "ServerResource",
    "ValidationError",
    "adheres_to_server_schema",
    "server_config_from_raw",
]
