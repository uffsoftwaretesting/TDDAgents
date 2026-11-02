"""
The `needs-auth` connection state: the 15-minute needs-auth cache, a 401 on connect over the
real sse / http transports, a stored token reaching a real MCP server (and being refreshed
first), the manager's skip, and `reconnect_mcp_server_impl`. Every server is on loopback.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

import app.services.mcp.client as mc
import app.services.mcp.mcp_connection_manager as mm
from app.services.mcp import auth as ma
from app.services.mcp.sdk_auth import UnauthorizedError
from app.services.mcp.types import (
    ConnectedMCPServer,
    FailedMCPServer,
    McpServerConfig,
    NeedsAuthMCPServer,
)
from app.utils.secure_storage.index import get_secure_storage
from tests.oauth_doubles import Reply, fake_http_server, install_authorization_server
from tests.test_mcp_client import demo_server, factory

ASGI = Callable[[dict[str, Any], Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]]


def remote(kind: str, url: str, **headers: str) -> McpServerConfig:
    return McpServerConfig(type=kind, scope="user", url=url, headers=headers or None)


def entry(name: str, config: McpServerConfig) -> dict[str, Any]:
    found = (get_secure_storage().read() or {}).get("mcpOAuth", {}).get(ma.get_server_key(name, config))
    return found if isinstance(found, dict) else {}


def put(name: str, config: McpServerConfig, **fields: Any) -> None:
    data = get_secure_storage().read() or {}
    data.setdefault("mcpOAuth", {})[ma.get_server_key(name, config)] = fields
    get_secure_storage().update(data)


def connect(name: str, config: McpServerConfig) -> Any:
    return asyncio.run(mc.connect_to_server(name, config, cwd="/w"))


# ── the needs-auth cache ──


def test_the_cache_records_a_server_for_fifteen_minutes(monkeypatch):
    assert mc.MCP_AUTH_CACHE_TTL_MS == 15 * 60 * 1000
    assert not mc.is_mcp_auth_cached("a")
    now = 1_000_000.0
    monkeypatch.setattr("app.services.mcp.client.time", SimpleNamespace(time=lambda: now))
    mc.set_mcp_auth_cache_entry("a")
    mc.set_mcp_auth_cache_entry("b")
    assert json.loads(mc.get_mcp_auth_cache_path().read_text()) == {
        "a": {"timestamp": 1_000_000_000}, "b": {"timestamp": 1_000_000_000}}
    assert mc.get_mcp_auth_cache_path().name == "mcp-needs-auth-cache.json"
    monkeypatch.setattr("app.services.mcp.client.time", SimpleNamespace(time=lambda: now + 15 * 60 - 0.001))
    assert mc.is_mcp_auth_cached("a") and not mc.is_mcp_auth_cached("c")
    monkeypatch.setattr("app.services.mcp.client.time", SimpleNamespace(time=lambda: now + 15 * 60))
    assert not mc.is_mcp_auth_cached("a")


def test_clearing_and_unreadable_caches():
    mc.set_mcp_auth_cache_entry("a")
    mc.clear_mcp_auth_cache()
    assert not mc.get_mcp_auth_cache_path().exists() and not mc.is_mcp_auth_cached("a")
    mc.clear_mcp_auth_cache()
    for broken in ("not json", "[1]", '{"a": {"timestamp": "now"}}', '{"a": 1}'):
        mc.get_mcp_auth_cache_path().write_text(broken)
        assert not mc.is_mcp_auth_cached("a")
    mc.get_mcp_auth_cache_path().write_text("not json")
    mc.set_mcp_auth_cache_entry("b")
    assert mc.is_mcp_auth_cached("b")


def test_a_cache_that_cannot_be_written_is_ignored(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(mc, "config_home", lambda: blocker / "home")
    mc.set_mcp_auth_cache_entry("a")
    assert not mc.is_mcp_auth_cached("a")


def test_handle_remote_auth_failure_caches_and_answers_needs_auth(caplog):
    config = remote("sse", "https://m/sse")
    with caplog.at_level("DEBUG", logger=mc.logger.name):
        result = asyncio.run(mc.handle_remote_auth_failure("srv", config, "sse"))
    assert result == NeedsAuthMCPServer(name="srv", config=config) and result.type == "needs-auth"
    assert mc.is_mcp_auth_cached("srv")
    assert "Authentication required for SSE server" in caplog.text


# ── a 401 on connect ──


CHALLENGE = "Bearer resource_metadata=\"{base}/.well-known/oauth-protected-resource/mcp\", scope=\"read\""


@pytest.mark.parametrize("kind,method", [("http", "POST"), ("sse", "GET")])
def test_a_401_without_usable_credentials_is_needs_auth(kind, method):
    with fake_http_server() as srv:
        install_authorization_server(srv, scopes=["read"])
        srv.route(method, "/mcp", Reply(401, {"error": "invalid_token"},
                                        {"WWW-Authenticate": CHALLENGE.format(base=srv.base)}))
        config = remote(kind, f"{srv.base}/mcp")
        result = connect("srv", config)
        assert isinstance(result, NeedsAuthMCPServer) and result.config is config
        assert mc.is_mcp_auth_cached("srv")
        stored = entry("srv", config)
        assert stored["clientId"] == "client-1" and stored["stepUpScope"] == "read"
        assert stored["discoveryState"]["authorizationServerUrl"] == srv.base
        assert ma.has_mcp_discovery_but_no_token("srv", config)
        assert srv.requests(method, "/mcp")[0].headers.get("Authorization") is None


def test_other_failures_are_failed_servers_and_not_cached():
    with fake_http_server() as srv:
        srv.route("POST", "/mcp", Reply(500, {"error": "boom"}))
        result = connect("srv", remote("http", f"{srv.base}/mcp"))
        assert isinstance(result, FailedMCPServer) and not mc.is_mcp_auth_cached("srv")


def test_a_401_after_a_completed_refresh_is_a_failure():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        srv.route("POST", "/mcp", Reply(401, {"error": "invalid_token"}))
        config = remote("http", f"{srv.base}/mcp")
        put("srv", config, serverName="srv", serverUrl=config.url, accessToken="old", refreshToken="r0",
            expiresAt=time.time() * 1000 + 3600_000, clientId="cid",
            discoveryState={"authorizationServerUrl": srv.base})
        result = connect("srv", config)
        assert isinstance(result, FailedMCPServer)
        assert result.error == "Server returned 401 after successful authentication"
        assert [r.headers["Authorization"] for r in srv.requests("POST", "/mcp")] == ["Bearer old", "Bearer tok-1"]
        assert not mc.is_mcp_auth_cached("srv")


def test_is_unauthorized_looks_inside_exception_groups():
    nested = ExceptionGroup("outer", [ValueError("x"), ExceptionGroup("inner", [UnauthorizedError()])])
    assert mc._is_unauthorized(nested)
    assert not mc._is_unauthorized(ExceptionGroup("g", [ValueError("x")]))
    assert not mc._is_unauthorized(None)


# ── a real MCP server behind a bearer check ──


def guarded(app: ASGI, accepted: set[str], seen: list[str | None]) -> ASGI:
    async def asgi(scope: dict[str, Any], receive: Callable[[], Awaitable[Any]],
                   send: Callable[[Any], Awaitable[None]]) -> None:
        if scope["type"] == "http":
            header = dict(scope["headers"]).get(b"authorization")
            value = header.decode() if header else None
            seen.append(value)
            if value not in accepted:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
        await app(scope, receive, send)

    return asgi


@contextmanager
def mcp_http_server(accepted: set[str]) -> Iterator[tuple[str, list[str | None]]]:
    seen: list[str | None] = []
    server = MCPServer("demo")

    @server.tool()
    def ping() -> str:
        return "pong"

    app = guarded(server.streamable_http_app(), accepted, seen)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        assert time.monotonic() < deadline and thread.is_alive(), "uvicorn did not start"
        time.sleep(0.01)
    port = uv.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp", seen
    finally:
        uv.should_exit = True
        thread.join(10)


async def _connect_and_list(name: str, config: McpServerConfig) -> tuple[Any, list[str]]:
    client = await mc.connect_to_server(name, config, cwd="/w")
    if not isinstance(client, ConnectedMCPServer):
        return client, []
    try:
        return client, [t.name for t in await mc.fetch_tools_for_client(client)]
    finally:
        assert client.cleanup is not None
        await client.cleanup()


def test_a_stored_token_reaches_the_server():
    with mcp_http_server({"Bearer good"}) as (url, seen):
        config = remote("http", url)
        put("srv", config, accessToken="good", expiresAt=time.time() * 1000 + 3600_000)
        client, tools = asyncio.run(_connect_and_list("srv", config))
        assert isinstance(client, ConnectedMCPServer) and tools == ["ping"]
        assert seen and set(seen) == {"Bearer good"}


def test_a_configured_authorization_header_wins_over_the_stored_token():
    with mcp_http_server({"Bearer static"}) as (url, seen):
        config = remote("http", url, Authorization="Bearer static")
        put("srv", config, accessToken="stored", expiresAt=time.time() * 1000 + 3600_000)
        client, _ = asyncio.run(_connect_and_list("srv", config))
        assert isinstance(client, ConnectedMCPServer) and set(seen) == {"Bearer static"}


def test_an_expiring_token_is_refreshed_before_connecting():
    with fake_http_server() as auth_server, mcp_http_server({"Bearer tok-1"}) as (url, seen):
        install_authorization_server(auth_server)
        config = remote("http", url)
        put("srv", config, serverName="srv", serverUrl=url, accessToken="old", refreshToken="r0",
            expiresAt=time.time() * 1000 + 60_000, clientId="cid",
            discoveryState={"authorizationServerUrl": auth_server.base})
        client, tools = asyncio.run(_connect_and_list("srv", config))
        assert isinstance(client, ConnectedMCPServer) and tools == ["ping"]
        assert set(seen) == {"Bearer tok-1"}
        refresh = auth_server.requests("POST", "/token")[0].form
        assert refresh["grant_type"] == "refresh_token" and refresh["refresh_token"] == "r0"
        assert entry("srv", config)["accessToken"] == "tok-1"


# ── the manager and reconnect ──


def test_the_manager_skips_servers_known_to_need_auth(monkeypatch):
    attempted: list[str] = []

    def no_transport(name: str, config: McpServerConfig) -> Any:
        attempted.append(name)
        raise AssertionError("must not connect")

    cached = remote("http", "https://cached/mcp")
    probed = remote("sse", "https://probed/sse")
    put("probed", probed, serverName="probed", accessToken="", expiresAt=0)
    mc.set_mcp_auth_cache_entry("cached")
    manager = mm.McpConnectionManager(cwd="/w", transport_factory=no_transport)
    asyncio.run(manager.connect({"cached": cached, "probed": probed}))
    assert sorted((c.name, c.type) for c in manager.clients) == [("cached", "needs-auth"), ("probed", "needs-auth")]
    assert attempted == [] and manager.tools == []


def test_the_needs_auth_skip_is_only_for_remote_servers():
    attempted: list[str] = []
    stdio = McpServerConfig(type="stdio", scope="user", command="x")
    mc.set_mcp_auth_cache_entry("local")

    def record(name: str, config: McpServerConfig) -> Any:
        attempted.append(name)
        raise RuntimeError("no transport")

    manager = mm.McpConnectionManager(cwd="/w", transport_factory=record)
    asyncio.run(manager.connect({"local": stdio}))
    assert attempted == ["local"] and manager.clients[0].type == "failed"


def test_reconnect_returns_the_servers_tools_and_resources(monkeypatch):
    cleared: list[bool] = []
    monkeypatch.setattr(mm, "clear_keychain_cache", lambda: cleared.append(True))
    config = McpServerConfig(type="stdio", scope="user", command="unused")

    async def scenario() -> mm.ReconnectResult:
        result = await mm.reconnect_mcp_server_impl("demo", config, cwd="/w", transport_factory=factory(demo_server()))
        assert isinstance(result.client, ConnectedMCPServer) and result.client.cleanup is not None
        await result.client.cleanup()
        return result

    result = asyncio.run(scenario())
    names = [t.name for t in result.tools]
    assert "mcp__demo__add" in names and names[-2:] == ["ListMcpResourcesTool", "ReadMcpResourceTool"]
    assert result.resources is not None and {r.name for r in result.resources} == {"greeting", "blob"}
    assert result.commands == [] and cleared == [True]


def test_reconnect_without_resources_or_a_connection(monkeypatch):
    config = McpServerConfig(type="stdio", scope="user", command="unused")
    plain = MCPServer("plain")

    @plain.tool()
    def only() -> str:
        return "x"

    async def scenario() -> mm.ReconnectResult:
        result = await mm.reconnect_mcp_server_impl("plain", config, cwd="/w", transport_factory=factory(plain))
        assert isinstance(result.client, ConnectedMCPServer) and result.client.cleanup is not None
        await result.client.cleanup()
        return result

    result = asyncio.run(scenario())
    # The SDK server always advertises resources: the resource tools come, an empty list does not.
    assert result.tools[0].name == "mcp__plain__only" and result.resources is None

    def broken(name: str, config: McpServerConfig) -> Any:
        raise RuntimeError("down")

    failed = asyncio.run(mm.reconnect_mcp_server_impl("x", config, cwd="/w", transport_factory=broken))
    assert isinstance(failed.client, FailedMCPServer) and failed.tools == [] and failed.resources is None

    async def explode(client: Any) -> Any:
        raise RuntimeError("listing broke")

    monkeypatch.setattr(mm, "fetch_tools_for_client", explode)

    async def failing_listing() -> mm.ReconnectResult:
        return await mm.reconnect_mcp_server_impl("plain", config, cwd="/w", transport_factory=factory(plain))

    errored = asyncio.run(failing_listing())
    assert isinstance(errored.client, FailedMCPServer) and errored.client.name == "plain"
