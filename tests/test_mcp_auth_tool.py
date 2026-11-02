"""
`createMcpAuthTool` (`app/tools/mcp_auth_tool/`) and the `McpConnectionManager` offering it
beside a `needs-auth` server: OAuth flows are fakes, servers are in-process.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

import app.services.mcp.client as mc
import app.services.mcp.mcp_connection_manager as mm
import app.tools.mcp_auth_tool.mcp_auth_tool as mat
from app.services.mcp.sdk_auth import UnauthorizedError
from app.services.mcp.types import ConnectedMCPServer, McpServerConfig, NeedsAuthMCPServer
from app.tools.mcp_tool.mcp_tool import McpBuiltTool
from app.utils.abort_controller import AbortSignal
from tests.test_mcp_client import demo_server, factory

HTTP = McpServerConfig(type="http", scope="user", url="https://remote.example/mcp")
SSE = McpServerConfig(type="sse", scope="user", url="https://remote.example/sse")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class Flow:
    """A fake `perform_mcp_oauth_flow`: optionally reports a URL, then waits for `finish`."""

    def __init__(self, url: str | None = "https://auth.example/authorize?x=1", error: Exception | None = None,
                 error_after_url: bool = False) -> None:
        self.url = url
        self.error = error
        self.error_after_url = error_after_url
        self.finish = asyncio.Event()
        self.calls: list[tuple[Any, ...]] = []

    async def __call__(self, server_name: str, config: McpServerConfig, on_authorization_url: Callable[[str], None],
                       signal: Any, **kwargs: Any) -> None:
        self.calls.append((server_name, config, signal, kwargs))
        if self.error is not None and not self.error_after_url:
            raise self.error
        if self.url is None:
            return
        on_authorization_url(self.url)
        on_authorization_url("https://ignored.example/second")
        await self.finish.wait()
        if self.error is not None:
            raise self.error


class Recorded:
    """`reconnect` + `apply_reconnect` doubles."""

    def __init__(self) -> None:
        self.reconnected: list[tuple[str, McpServerConfig]] = []
        self.applied: list[tuple[str, Any]] = []
        self.result = mm.ReconnectResult(client=NeedsAuthMCPServer(name="remote", config=HTTP), tools=["t1", "t2"])

    async def reconnect(self, name: str, config: McpServerConfig) -> Any:
        self.reconnected.append((name, config))
        return self.result

    def apply(self, name: str, result: Any) -> None:
        self.applied.append((name, result))


def make(config: McpServerConfig = HTTP, flow: Any = None, name: str = "remote",
         background: set[asyncio.Task[None]] | None = None) -> tuple[McpBuiltTool, Recorded, set[asyncio.Task[None]]]:
    recorded = Recorded()
    tasks: set[asyncio.Task[None]] = background if background is not None else set()
    tool = mat.create_mcp_auth_tool(name, config, reconnect=recorded.reconnect, apply_reconnect=recorded.apply,
                                    background=tasks, perform_oauth_flow=flow)
    return tool, recorded, tasks


# ── the tool's shape ──


def test_the_tool_describes_the_server_and_is_always_allowed():
    tool, _, _ = make()
    expected = (
        "The `remote` MCP server (http at https://remote.example/mcp) is installed but requires authentication. "
        "Call this tool to start the OAuth flow — you'll receive an authorization URL to share with the user. "
        "Once the user completes authorization in their browser, the server's real tools will become available "
        "automatically.")
    assert tool.name == "mcp__remote__authenticate"
    assert tool.prompt == expected and tool.description({}) == expected
    assert (tool.server_name, tool.mcp_tool_name) == ("remote", "authenticate")
    assert tool.is_mcp and tool.is_enabled()
    assert not tool.is_read_only({}) and not tool.is_concurrency_safe({})
    assert tool.max_result_size_chars == 10_000
    assert tool.user_facing_name(None) == "remote - authenticate (MCP)"
    assert tool.render_tool_use_message({}, False) == "Authenticate remote MCP server"
    assert tool.to_auto_classifier_input({}) == "remote"
    assert tool.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    decision = run(tool.check_permissions({}, None))  # type: ignore[arg-type]
    assert (decision.behavior, decision.updated_input) == ("allow", {})


@pytest.mark.parametrize("config,location", [
    (McpServerConfig(type="stdio", scope="user", command="x"), "stdio"),
    (McpServerConfig(type=None, scope="user", command="x"), "stdio"),
    (SSE, "sse at https://remote.example/sse"),
])
def test_the_location_names_the_transport_and_url(config, location):
    tool, _, _ = make(config)
    assert f"MCP server ({location}) is installed" in tool.prompt
    assert mat.get_config_url(config) == config.url


def test_claude_ai_connectors_and_local_transports_are_unsupported():
    flow = Flow()
    proxy, _, _ = make(McpServerConfig(type="claudeai-proxy", scope="claudeai", url="https://c"), flow, "conn")
    result = run(proxy.call({}, None))  # type: ignore[arg-type]
    assert result.metadata == {"status": "unsupported", "message": result.content}
    assert result.content == ('This is a claude.ai MCP connector. Ask the user to run /mcp and select "conn" to '
                              "authenticate.")
    for config, transport in ((McpServerConfig(type="ws", scope="user", url="wss://w"), "ws"),
                              (McpServerConfig(type=None, scope="user", command="x"), "stdio")):
        local, _, _ = make(config, flow, "loc")
        out = run(local.call({}, None))  # type: ignore[arg-type]
        assert out.metadata == {"status": "unsupported", "message": out.content}
        assert out.content == (f'Server "loc" uses {transport} transport which does not support OAuth from this '
                               "tool. Ask the user to run /mcp and authenticate manually.")
    assert flow.calls == []


# ── the flow ──


def test_the_url_is_returned_and_success_reconnects_in_the_background():
    flow = Flow()
    mc.set_mcp_auth_cache_entry("remote")

    async def scenario() -> tuple[Any, Recorded, int]:
        tool, recorded, tasks = make(flow=flow)
        result = await tool.call({}, None)  # type: ignore[arg-type]
        pending = len(tasks)
        assert recorded.reconnected == [] and mc.is_mcp_auth_cached("remote")
        flow.finish.set()
        await asyncio.gather(*tasks)
        return result, recorded, pending

    result, recorded, pending = run(scenario())
    url = "https://auth.example/authorize?x=1"
    assert result.metadata == {"status": "auth_url", "message": result.content, "authUrl": url}
    assert result.content == (
        f"Ask the user to open this URL in their browser to authorize the remote MCP server:\n\n{url}\n\n"
        "Once they complete the flow, the server's tools will become available automatically.")
    [(name, config, signal, kwargs)] = flow.calls
    assert (name, config, kwargs) == ("remote", HTTP, {"skip_browser_open": True})
    assert isinstance(signal, AbortSignal) and not signal.aborted
    assert pending == 1
    assert recorded.reconnected == [("remote", HTTP)]
    assert recorded.applied == [("remote", recorded.result)]
    assert not mc.is_mcp_auth_cached("remote")


def test_silent_auth_completes_without_a_url():
    flow = Flow(url=None)

    async def scenario() -> tuple[Any, Recorded, set[asyncio.Task[None]]]:
        tool, recorded, tasks = make(flow=flow)
        result = await tool.call({}, None)  # type: ignore[arg-type]
        await asyncio.gather(*tasks)
        return result, recorded, tasks

    result, recorded, tasks = run(scenario())
    assert result.metadata == {"status": "auth_url", "message": result.content}
    assert result.content == ("Authentication completed silently for remote. The server's tools should now be "
                              "available.")
    assert recorded.applied == [("remote", recorded.result)] and tasks == set()


def test_a_flow_that_fails_to_start_is_an_error_result(caplog):
    flow = Flow(error=RuntimeError("no metadata"))

    async def scenario() -> tuple[Any, Recorded]:
        tool, recorded, tasks = make(flow=flow)
        result = await tool.call({}, None)  # type: ignore[arg-type]
        await asyncio.gather(*tasks)
        return result, recorded

    with caplog.at_level(logging.ERROR, logger=mat.__name__):
        result, recorded = run(scenario())
    assert result.metadata == {"status": "error", "message": result.content}
    assert result.content == ("Failed to start OAuth flow for remote: no metadata. Ask the user to run /mcp and "
                              "authenticate manually.")
    assert recorded.reconnected == []
    assert caplog.messages == ["MCP server remote: OAuth flow failed after tool-triggered start: no metadata"]


def test_a_flow_that_fails_after_the_url_is_logged(caplog):
    flow = Flow(error=RuntimeError("denied"), error_after_url=True)

    async def scenario() -> tuple[Any, Recorded]:
        tool, recorded, tasks = make(flow=flow)
        result = await tool.call({}, None)  # type: ignore[arg-type]
        flow.finish.set()
        await asyncio.gather(*tasks)
        return result, recorded

    with caplog.at_level(logging.ERROR, logger=mat.__name__):
        result, recorded = run(scenario())
    assert result.metadata["status"] == "auth_url" and recorded.applied == []
    assert caplog.messages == ["MCP server remote: OAuth flow failed after tool-triggered start: denied"]


def test_a_cancelled_flow_cancels_the_call():
    async def cancelled(*_args: Any, **_kwargs: Any) -> None:
        raise asyncio.CancelledError

    async def scenario() -> None:
        tool, _, tasks = make(flow=cancelled)
        with pytest.raises(asyncio.CancelledError):
            await tool.call({}, None)  # type: ignore[arg-type]
        await asyncio.gather(*tasks, return_exceptions=True)

    run(scenario())


def test_the_real_flow_is_used_by_default(monkeypatch):
    flow = Flow(url=None)
    monkeypatch.setattr(mat, "perform_mcp_oauth_flow", flow)

    async def scenario() -> None:
        tool, _, tasks = make()
        await tool.call({}, None)  # type: ignore[arg-type]
        await asyncio.gather(*tasks)

    run(scenario())
    assert [c[0] for c in flow.calls] == ["remote"]


# ── the manager offers it and applies the reconnect ──


def unauthorized_then(server_factory: Callable[[str, McpServerConfig], Any]) -> Callable[[str, McpServerConfig], Any]:
    """A transport that answers 401 once, then connects to the in-process server."""
    attempts: list[str] = []

    def transport(name: str, config: McpServerConfig) -> Any:
        attempts.append(name)
        if len(attempts) == 1:
            @asynccontextmanager
            async def refused() -> Any:
                raise UnauthorizedError()
                yield  # pragma: no cover - never reached

            return refused()
        return server_factory(name, config)

    return transport


def test_a_needs_auth_server_gets_the_auth_tool_and_its_success_swaps_the_tools_in(monkeypatch):
    flow = Flow()
    monkeypatch.setattr(mat, "perform_mcp_oauth_flow", flow)
    told: list[tuple[str, Any]] = []
    other = McpServerConfig(type="stdio", scope="user", command="x")

    async def scenario() -> tuple[list[str], list[str], Any]:
        transport = unauthorized_then(factory(demo_server()))
        manager = mm.McpConnectionManager(cwd="/w", transport_factory=lambda n, c: (
            factory(demo_server())(n, c) if n == "local" else transport(n, c)),
            on_reconnect=lambda n, r: told.append((n, r)))
        await manager.connect({"remote": HTTP, "local": other})
        before = sorted(t.name for t in manager.tools)
        assert [type(c).__name__ for c in manager.clients if c.name == "remote"] == ["NeedsAuthMCPServer"]
        assert mc.is_mcp_auth_cached("remote")
        [auth] = [t for t in manager.tools if t.name == "mcp__remote__authenticate"]
        result = await auth.call({}, None)
        flow.finish.set()
        await asyncio.gather(*manager._auth_flows)
        after = sorted(t.name for t in manager.tools)
        try:
            remote = next(c for c in manager.clients if c.name == "remote")
            assert isinstance(remote, ConnectedMCPServer)
            assert not mc.is_mcp_auth_cached("remote")
        finally:
            await manager.aclose()
        return before, after, result

    before, after, result = run(scenario())
    assert "mcp__remote__authenticate" in before and "mcp__local__add" in before
    assert "mcp__remote__add" not in before
    assert "mcp__remote__authenticate" not in after
    assert "mcp__remote__add" in after and "mcp__local__add" in after
    assert result.metadata["status"] == "auth_url"
    [(name, reconnect_result)] = told
    assert name == "remote" and isinstance(reconnect_result.client, ConnectedMCPServer)


def test_apply_reconnect_replaces_only_that_servers_client_and_tools():
    manager = mm.McpConnectionManager(cwd="/w")
    a, b = NeedsAuthMCPServer(name="a", config=HTTP), NeedsAuthMCPServer(name="b", config=HTTP)
    manager.clients.extend([a, b])
    names = ("mcp__a__authenticate", "mcp__ab__x", "mcp__b__authenticate", "ListMcpResourcesTool")
    tools = [type("T", (), {"name": n})() for n in names]
    manager.tools.extend(tools)
    clients_ref, tools_ref = manager.clients, manager.tools
    new_client = ConnectedMCPServer(name="a", config=HTTP, session=None, capabilities=None)
    manager.apply_reconnect("a", mm.ReconnectResult(client=new_client, tools=["new"]))
    assert manager.clients == [new_client, b] and manager.clients is clients_ref
    assert manager.tools == [tools[1], tools[2], tools[3], "new"] and manager.tools is tools_ref


def test_aclose_cancels_a_flow_still_running(monkeypatch):
    flow = Flow()
    monkeypatch.setattr(mat, "perform_mcp_oauth_flow", flow)

    async def scenario() -> list[bool]:
        manager = mm.McpConnectionManager(cwd="/w", transport_factory=unauthorized_then(factory(demo_server())))
        await manager.connect({"remote": HTTP})
        [auth] = manager.tools
        await auth.call({}, None)
        flows = list(manager._auth_flows)
        await manager.aclose()
        return [f.cancelled() for f in flows] + [not manager._auth_flows]

    assert run(scenario()) == [True, True]
