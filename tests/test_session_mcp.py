"""The session's live MCP servers: `SessionMcp`, the SDK control transport, and the MCP control requests."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.cli.print import HeadlessOptions, run_headless
from app.cli.session_mcp import BLOCKED_BY_POLICY, SessionMcp, mcp_prefix, to_scoped_config
from app.services.mcp.config import get_mcp_config_by_name, is_mcp_server_disabled
from app.services.mcp.mcp_connection_manager import ReconnectResult
from app.services.mcp.sdk_control_transport import SdkControlClientTransport, setup_sdk_mcp_clients
from app.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpServerConfig,
)
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.settings.settings import SettingsLocations

STDIO = McpServerConfig(type="stdio", scope="project", command="srv")


def tool(name: str) -> Any:
    return SimpleNamespace(name=name)


def manager(*clients: Any, tools: list[Any] | None = None) -> Any:
    return SimpleNamespace(clients=list(clients), tools=list(tools or []))


class Cleanup:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


def connected(name: str, config: McpServerConfig = STDIO, cleanup: Any = None) -> ConnectedMCPServer:
    return ConnectedMCPServer(name=name, config=config, session=None, capabilities=None, cleanup=cleanup)


# ── views ────────────────────────────────────────────────────────────────────

def test_views_span_every_server_list_and_keep_the_first_tool_of_a_name() -> None:
    first, second = tool("mcp__a__x"), tool("mcp__a__x")
    session = SessionMcp(manager=manager(connected("a"), tools=[first]), cwd=".")
    session.sdk_clients = [connected("s")]
    session.sdk_tools = [second, tool("mcp__s__y")]
    session.dynamic_clients = [connected("d", McpServerConfig(type="http", scope="dynamic", url="u"))]
    session.dynamic_tools = [tool("mcp__d__z")]
    assert [c.name for c in session.all_clients()] == ["a", "s", "d"]
    assert [t.name for t in session.all_tools()] == ["mcp__a__x", "mcp__s__y", "mcp__d__z"]
    assert session.all_tools()[0] is first
    assert session.find_config("d") == McpServerConfig(type="http", scope="dynamic", url="u")
    assert session.find_config("missing") is None
    assert [s["name"] for s in session.statuses()] == ["a", "s", "d"]


def test_prefix_and_scoped_config() -> None:
    assert mcp_prefix("my server.v2") == "mcp__my_server_v2__"
    assert to_scoped_config({"type": "sdk", "name": "x"}) == McpServerConfig(type="sdk", scope="dynamic")
    scoped = to_scoped_config({"type": "stdio", "command": "run", "args": ["-q"]})
    assert (scoped.scope, scoped.command, scoped.args) == ("dynamic", "run", ("-q",))


def test_find_config_reads_the_configured_servers_first(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"proj": {"command": "p"},
                                                                   "both": {"command": "from-file"}}}))
    managed = tmp_path / "etc"
    managed.mkdir()
    locations = SettingsLocations(project_root=tmp_path, home=tmp_path / "home", managed_dir=managed)
    session = SessionMcp(manager=manager(connected("both"), connected("live")), cwd=str(tmp_path),
                         settings_locations=lambda: locations)
    proj = session.find_config("proj")
    assert proj is not None and (proj.command, proj.scope) == ("p", "project")
    both = session.find_config("both")
    assert both is not None and both.command == "from-file"
    assert session.find_config("live") == STDIO and session.find_config("none") is None
    # Restricted to plugins: only enterprise servers are reachable by name.
    (managed / "managed-settings.json").write_text(json.dumps({"strictPluginOnlyCustomization": ["mcp"]}))
    assert get_mcp_config_by_name("proj", tmp_path, locations) is None
    (managed / "managed-mcp.json").write_text(json.dumps({"mcpServers": {"proj": {"command": "corp"}}}))
    corp = get_mcp_config_by_name("proj", tmp_path, locations)
    assert corp is not None and (corp.command, corp.scope) == ("corp", "enterprise")


# ── reconnect / toggle ───────────────────────────────────────────────────────

def test_apply_reconnect_replaces_a_configured_servers_client_and_tools() -> None:
    changes: list[int] = []
    session = SessionMcp(manager=manager(connected("a"), connected("b"),
                                         tools=[tool("mcp__a__old"), tool("mcp__b__keep")]),
                         cwd=".", on_change=lambda: changes.append(1))
    fresh = connected("a")
    session.apply_reconnect("a", ReconnectResult(client=fresh, tools=[tool("mcp__a__new")]))
    assert session.manager.clients[0] is fresh and [c.name for c in session.manager.clients] == ["a", "b"]
    assert [t.name for t in session.manager.tools] == ["mcp__b__keep", "mcp__a__new"]
    session.apply_reconnect("c", ReconnectResult(client=connected("c")))
    assert [c.name for c in session.manager.clients] == ["a", "b", "c"]
    assert changes == [1, 1]


def test_apply_reconnect_of_a_dynamic_server_stays_dynamic() -> None:
    session = SessionMcp(manager=manager(), cwd=".")
    session.dynamic_configs = {"d": STDIO}
    session.dynamic_clients = [connected("d"), connected("e")]
    session.dynamic_tools = [tool("mcp__d__old"), tool("mcp__e__keep")]
    fresh = connected("d")
    session.apply_reconnect("d", ReconnectResult(client=fresh, tools=[tool("mcp__d__new")]))
    assert [c.name for c in session.dynamic_clients] == ["e", "d"] and session.dynamic_clients[1] is fresh
    assert [t.name for t in session.dynamic_tools] == ["mcp__e__keep", "mcp__d__new"]
    assert session.manager.clients == []


def test_reconnect_runs_the_impl_and_applies_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def impl(name: str, config: Any, **kwargs: Any) -> ReconnectResult:
        calls.append((name, config, kwargs))
        return ReconnectResult(client=FailedMCPServer(name=name, config=config, error="boom"))

    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", impl)
    session = SessionMcp(manager=manager(connected("a")), cwd="/w", connect_options={"elicitation_callback": "cb"})
    result = asyncio.run(session.reconnect("a", STDIO))
    assert calls == [("a", STDIO, {"cwd": "/w", "elicitation_callback": "cb"})]
    assert session.manager.clients == [result.client] == [FailedMCPServer(name="a", config=STDIO, error="boom")]


def test_disable_persists_disconnects_and_drops_the_tools(tmp_path: Path) -> None:
    cleanup = Cleanup()
    changes: list[int] = []
    session = SessionMcp(manager=manager(connected("a", cleanup=cleanup), tools=[tool("mcp__a__x"), tool("mcp__b__y")]),
                         cwd=str(tmp_path), on_change=lambda: changes.append(1))
    session.dynamic_clients = [connected("a")]
    session.dynamic_tools = [tool("mcp__a__z")]
    asyncio.run(session.disable("a", STDIO))
    assert is_mcp_server_disabled("a", tmp_path)
    assert cleanup.calls == 1 and changes == [1]
    assert session.manager.clients == [DisabledMCPServer(name="a", config=STDIO)]
    assert session.dynamic_clients == [DisabledMCPServer(name="a", config=STDIO)]
    assert [t.name for t in session.manager.tools] == ["mcp__b__y"] and session.dynamic_tools == []


def test_enable_persists_then_reconnects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def impl(name: str, config: Any, **kwargs: Any) -> ReconnectResult:
        return ReconnectResult(client=connected(name), tools=[tool("mcp__a__x")])

    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", impl)
    session = SessionMcp(manager=manager(DisabledMCPServer(name="a", config=STDIO)), cwd=str(tmp_path))
    asyncio.run(session.disable("a", STDIO))
    result = asyncio.run(session.enable("a", STDIO))
    assert not is_mcp_server_disabled("a", tmp_path)
    assert result.client.type == "connected" and [t.name for t in session.manager.tools] == ["mcp__a__x"]


# ── SDK servers over the control transport ───────────────────────────────────

class Host:
    """An SDK host's MCP server, answering JSON-RPC by hand."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []
        self.fail_on = fail_on

    async def __call__(self, server: str, message: dict[str, Any]) -> Any:
        self.received.append((server, message))
        method = message.get("method")
        if method is not None and method == self.fail_on:
            raise RuntimeError("host went away")
        if "id" not in message or method is None:
            return {"jsonrpc": "2.0", "id": 0, "result": {}}   # an answer the transport must drop
        result: dict[str, Any] = {}
        if method == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": server, "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": f"{server}:{message['params']['arguments']['x']}"}]}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def test_sdk_servers_connect_list_and_call_over_the_host() -> None:
    host = Host()

    async def scenario() -> Any:
        sdk = McpServerConfig(type="sdk", scope="dynamic")
        clients, tools, transports = await setup_sdk_mcp_clients({"calc": sdk}, host, cwd=".")
        (client,) = clients
        assert isinstance(client, ConnectedMCPServer) and client.config.scope == "dynamic"
        assert [t.name for t in tools] == ["mcp__calc__echo"] and set(transports) == {"calc"}
        answer = await client.session.call_tool("echo", {"x": 7})
        # The host's own request (a ping) reaches the client, whose answer goes back to the host.
        await transports["calc"].deliver({"jsonrpc": "2.0", "id": "p1", "method": "ping"})
        for _ in range(50):
            if any(m.get("id") == "p1" and "result" in m for _, m in host.received):
                break
            await asyncio.sleep(0.01)
        assert client.cleanup is not None
        await client.cleanup()
        assert transports["calc"].is_closed
        return answer

    answer = asyncio.run(scenario())
    assert answer.content[0].text == "calc:7"
    methods = [m.get("method") for _, m in host.received]
    assert methods[:2] == ["initialize", "notifications/initialized"] and "tools/call" in methods
    assert {"jsonrpc": "2.0", "id": "p1", "result": {}} in [m for _, m in host.received]
    assert {server for server, _ in host.received} == {"calc"}


def test_a_failed_host_message_fails_the_pending_request() -> None:
    host = Host(fail_on="tools/call")

    async def scenario() -> str:
        clients, _, _ = await setup_sdk_mcp_clients({"calc": McpServerConfig(type="sdk", scope="dynamic")},
                                                    host, cwd=".")
        try:
            await clients[0].session.call_tool("echo", {"x": 1})
        except Exception as error:  # noqa: BLE001 - the McpError is what the test inspects
            return str(error)
        finally:
            await clients[0].cleanup()
        return "no error"

    assert asyncio.run(asyncio.wait_for(scenario(), 10)) == "host went away"


def test_a_failed_sdk_connection_is_a_failed_server() -> None:
    async def connect(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("nope")

    clients, tools, _ = asyncio.run(setup_sdk_mcp_clients(
        {"calc": McpServerConfig(type="sdk", scope="dynamic")}, Host(), cwd=".", connect=connect))
    assert clients == [FailedMCPServer(name="calc", config=McpServerConfig(type="sdk", scope="user"))]
    assert tools == []


def test_deliver_before_the_streams_open_is_dropped() -> None:
    transport = SdkControlClientTransport("calc", Host())
    asyncio.run(transport.deliver({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))
    assert not transport.is_closed


def test_update_sdk_connects_only_when_the_set_changed() -> None:
    host = Host()
    changes: list[int] = []

    async def scenario() -> list[list[str]]:
        session = SessionMcp(manager=manager(), cwd=".", on_change=lambda: changes.append(1))
        session.add_sdk_servers(["calc"])
        await session.update_sdk()                      # no host yet: nothing
        seen = [[c.name for c in session.sdk_clients]]
        session.send_mcp_message = host
        await session.update_sdk()
        await session.update_sdk()                      # unchanged: no second connection
        seen.append([t.name for t in session.sdk_tools])
        result = await session.reconnect("calc", McpServerConfig(type="sdk", scope="dynamic"))
        seen.append([result.client.type, *(t.name for t in result.tools)])
        await session.deliver("calc", {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        await session.deliver("other", {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        await session.aclose()
        return seen

    seen = asyncio.run(scenario())
    assert seen == [[], ["mcp__calc__echo"], ["connected", "mcp__calc__echo"]]
    assert [m.get("method") for _, m in host.received].count("initialize") == 2
    assert changes == [1, 1]


# ── mcp_set_servers ──────────────────────────────────────────────────────────

def test_set_servers_adds_replaces_and_removes(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []
    cleanups = Cleanup()

    async def connect(name: str, config: Any, **kwargs: Any) -> Any:
        attempts.append(name)
        if name == "bad":
            return FailedMCPServer(name=name, config=config, error="refused")
        if name == "raises":
            raise RuntimeError("spawn failed")
        return connected(name, config, cleanup=cleanups)

    async def fetch(client: Any) -> list[Any]:
        return [SimpleNamespace(name="t")]

    monkeypatch.setattr("app.services.mcp.client.connect_to_server", connect)
    monkeypatch.setattr("app.services.mcp.client.fetch_tools_for_client", fetch)
    monkeypatch.setattr("app.tools.mcp_tool.mcp_tool.build_mcp_tool",
                        lambda client, d: tool(f"{mcp_prefix(client.name)}{d.name}"))

    async def scenario() -> list[Any]:
        session = SessionMcp(manager=manager(), cwd=".")
        first = await session.set_servers({
            "good": {"type": "stdio", "command": "g"}, "bad": {"type": "stdio", "command": "b"},
            "raises": {"type": "stdio", "command": "r"}, "host": {"type": "sdk", "name": "host"}})
        tools_after_first = [t.name for t in session.dynamic_tools]
        second = await session.set_servers({"good": {"type": "stdio", "command": "g2"}})
        return [first, tools_after_first, second, [c.name for c in session.dynamic_clients], session.sdk_configs]

    first, tools_after_first, second, clients, sdk = asyncio.run(scenario())
    assert first == ({"added": ["host", "good", "bad"], "removed": [],
                      "errors": {"bad": "refused", "raises": "spawn failed"}}, True)
    assert tools_after_first == ["mcp__good__t"]
    assert second == ({"added": ["good"], "removed": ["host", "bad", "raises"], "errors": {}}, True)
    assert clients == ["good"] and sdk == {}
    assert attempts == ["good", "bad", "raises", "good"] and cleanups.calls == 1


def test_set_servers_applies_the_enterprise_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    managed = tmp_path / "etc"
    managed.mkdir()
    (managed / "managed-settings.json").write_text(json.dumps({"deniedMcpServers": [{"serverName": "evil"}]}))

    async def connect(name: str, config: Any, **kwargs: Any) -> Any:
        return FailedMCPServer(name=name, config=config)

    monkeypatch.setattr("app.services.mcp.client.connect_to_server", connect)
    locations = SettingsLocations(project_root=tmp_path, home=tmp_path / "home", managed_dir=managed)
    session = SessionMcp(manager=manager(), cwd=str(tmp_path), settings_locations=lambda: locations)
    response, sdk_changed = asyncio.run(session.set_servers({
        "evil": {"type": "stdio", "command": "e"}, "fine": {"type": "stdio", "command": "f"}}))
    assert response == {"added": ["fine"], "removed": [], "errors": {"evil": BLOCKED_BY_POLICY,
                                                                     "fine": "Connection failed"}}
    assert not sdk_changed and set(session.dynamic_configs) == {"fine"}


# ── the control requests, end to end ─────────────────────────────────────────

def run_options(tmp_path: Path, model: Any) -> dict[str, Any]:
    from app.query.deps import get_production_deps

    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)
    return {"deps": dataclasses.replace(get_production_deps(), call_model=model), "permission_context": perm,
            "project_root": tmp_path, "home": tmp_path / "home", "managed_dir": tmp_path / "etc"}


class Model:
    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = list(turns)
        self.tool_names: list[list[str]] = []

    async def __call__(self, state: Any, config: Any) -> Any:
        self.tool_names.append([t.name for t in state.tool_context.tools])
        for message in self.turns.pop(0):
            yield message


class Feed:
    def __init__(self, *lines: dict[str, Any]) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        for line in lines:
            self.send(line)

    def send(self, message: dict[str, Any]) -> None:
        self.queue.put_nowait(json.dumps(message) + "\n")

    def close(self) -> None:
        self.queue.put_nowait(None)

    async def blocks(self) -> Any:
        while (item := await self.queue.get()) is not None:
            yield item


STREAM = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)


def control(request_id: str, subtype: str, **fields: Any) -> dict[str, Any]:
    return {"type": "control_request", "request_id": request_id, "request": {"subtype": subtype, **fields}}


def lines_of(written: list[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for chunk in written for line in chunk.splitlines() if line]


def test_mcp_control_requests_for_unknown_servers(python_workspace: Any, tmp_path: Path) -> None:
    feed = Feed(
        control("i", "initialize"),
        control("r", "mcp_reconnect", serverName="ghost"),
        control("t", "mcp_toggle", serverName="ghost", enabled=False),
        control("a", "mcp_authenticate", serverName="ghost"), control("c", "mcp_clear_auth", serverName="ghost"),
        control("u", "mcp_oauth_callback_url", serverName="ghost", callbackUrl="http://localhost/cb?code=1"),
        control("m", "mcp_message", server_name="ghost", message={"jsonrpc": "2.0", "method": "x"}),
        control("s", "mcp_status"), control("e", "end_session"),
    )
    written: list[str] = []
    asyncio.run(run_headless("sj-mcp-none", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=written.append,
                             **run_options(tmp_path, Model([]))))
    got = {m["response"]["request_id"]: m["response"] for m in lines_of(written) if m["type"] == "control_response"}
    assert got["r"]["error"] == got["t"]["error"] == "Server not found: ghost"
    assert got["a"]["error"] == got["c"]["error"] == "Server not found: ghost"
    assert got["u"]["error"] == "No active OAuth flow for server: ghost"
    assert got["m"] == {"subtype": "success", "request_id": "m"}
    assert got["s"]["response"] == {"mcpServers": []}


def test_sdk_server_from_the_host_reaches_the_model_and_set_servers(python_workspace: Any, tmp_path: Path) -> None:
    host = Host()
    feed = Feed(control("i", "initialize", sdkMcpServers=["calc"]),
                {"type": "user", "message": {"role": "user", "content": "go"}, "parent_tool_use_id": None})
    written: list[str] = []
    pending: list[asyncio.Task[Any]] = []
    phase = {"n": 0}

    async def answer(request_id: str, server: str, message: dict[str, Any]) -> None:
        reply = await host(server, message)
        feed.send({"type": "control_response", "response": {
            "subtype": "success", "request_id": request_id, "response": {"mcp_response": reply}}})

    def write(text: str) -> None:
        written.append(text)
        for message in lines_of([text]):
            request = message.get("request") or {}
            if message.get("type") == "control_request" and request.get("subtype") == "mcp_message":
                pending.append(asyncio.ensure_future(answer(message["request_id"], request["server_name"],
                                                            request["message"])))
            if message.get("type") == "result":
                phase["n"] += 1
                feed.send(control("st", "mcp_status"))
                feed.send(control("ss", "mcp_set_servers", servers={}))
                feed.send(control("st2", "mcp_status"))
                feed.send(control("e", "end_session"))

    model = Model([[AIMessage(content="done")]])
    asyncio.run(run_headless("sj-mcp-sdk", None, workspace=python_workspace, options=STREAM,
                             input_blocks=feed.blocks(), write_stdout=write, **run_options(tmp_path, model)))
    out = lines_of(written)
    got = {m["response"]["request_id"]: m["response"] for m in out if m["type"] == "control_response"}
    assert "mcp__calc__echo" in model.tool_names[0]
    (status,) = got["st"]["response"]["mcpServers"]
    assert (status["name"], status["status"], status["scope"]) == ("calc", "connected", "dynamic")
    assert status["tools"] == [{"name": "echo", "annotations": {}}]
    assert status["serverInfo"] == {"name": "calc", "version": "1"}
    assert got["ss"]["response"] == {"added": [], "removed": ["calc"], "errors": {}}
    assert got["st2"]["response"] == {"mcpServers": []}
    assert ("calc", "initialize") in [(s, m.get("method")) for s, m in host.received]


PROBE_SERVER = """
from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel


class Answer(BaseModel):
    name: str


srv = MCPServer("probe")


@srv.tool()
async def ask(ctx: Context) -> str:
    result = await ctx.elicit("Your name?", Answer)
    return f"{result.action}:{result.data.name}" if result.action == "accept" else result.action


@srv.tool()
async def visit(ctx: Context) -> str:
    result = await ctx.elicit_url("Sign in", "https://auth.example/x", "eid-1")
    await ctx.request_context.session.send_elicit_complete("eid-1")
    return "visited:" + result.action


srv.run()
"""


def test_a_configured_servers_elicitation_reaches_the_host(python_workspace: Any, tmp_path: Path) -> None:
    import sys

    from langchain_core.messages import ToolMessage

    (tmp_path / "probe_server.py").write_text(PROBE_SERVER)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"probe": {
        "command": sys.executable, "args": [str(tmp_path / "probe_server.py")]}}}))
    calls = AIMessage(content="", tool_calls=[{"name": "mcp__probe__ask", "id": "c1", "args": {}}])
    visit = AIMessage(content="", tool_calls=[{"name": "mcp__probe__visit", "id": "c2", "args": {}}])
    feed = Feed({"type": "user", "message": {"role": "user", "content": "go"}, "parent_tool_use_id": None})
    written: list[str] = []
    elicitations: list[dict[str, Any]] = []

    def write(text: str) -> None:
        written.append(text)
        for message in lines_of([text]):
            request = message.get("request") or {}
            answer: dict[str, Any] | None = None
            if message.get("type") == "control_request" and request.get("subtype") == "can_use_tool":
                answer = {"behavior": "allow", "updatedInput": request["input"]}
            if message.get("type") == "control_request" and request.get("subtype") == "elicitation":
                elicitations.append(request)
                answer = ({"action": "accept", "content": {"name": "Ada"}} if request.get("mode") != "url"
                          else {"action": "accept"})
            if answer is not None:
                feed.send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": message["request_id"], "response": answer}})
            if message.get("type") == "result":
                feed.close()

    model = Model([[calls], [visit], [AIMessage(content="Hello Ada.")]])
    seen: list[Any] = []
    inner = model.__call__

    async def recording(state: Any, config: Any) -> Any:
        seen.append(list(state.messages))
        async for message in inner(state, config):
            yield message

    options = dict(run_options(tmp_path, recording))
    options["permission_context"] = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    asyncio.run(asyncio.wait_for(run_headless(
        "sj-mcp-elicit", None, workspace=python_workspace, options=STREAM, input_blocks=feed.blocks(),
        write_stdout=write, **options), 60))
    form, url = elicitations
    assert (form["mcp_server_name"], form["message"], form["mode"]) == ("probe", "Your name?", "form")
    assert form["requested_schema"]["properties"]["name"]["type"] == "string"
    assert (url["mode"], url["url"], url["elicitation_id"]) == ("url", "https://auth.example/x", "eid-1")
    results = [str(m.content) for m in seen[-1] if isinstance(m, ToolMessage)]
    assert any("accept:Ada" in r for r in results) and any("visited:accept" in r for r in results)
    out = lines_of(written)
    assert out[-1]["result"] == "Hello Ada."
    complete = [m for m in out if m.get("subtype") == "elicitation_complete"]
    assert [(m["mcp_server_name"], m["elicitation_id"]) for m in complete] == [("probe", "eid-1")]


def test_find_client_and_a_close_that_fails_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def failing() -> None:
        raise RuntimeError("already gone")

    async def impl(name: str, config: Any, **kwargs: Any) -> ReconnectResult:
        return ReconnectResult(client=connected(name))

    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", impl)
    session = SessionMcp(manager=manager(connected("a", cleanup=failing)), cwd=str(tmp_path))
    assert session.find_client("a") is session.manager.clients[0] and session.find_client("z") is None
    result = asyncio.run(session.reconnect("a", STDIO))
    assert session.manager.clients == [result.client]
    session.manager.clients = [connected("a", cleanup=failing)]
    asyncio.run(session.disable("a", STDIO))
    assert session.manager.clients == [DisabledMCPServer(name="a", config=STDIO)]


def test_clear_server_records_it_failed_and_drops_its_tools() -> None:
    cleanup = Cleanup()
    changes: list[int] = []
    session = SessionMcp(manager=manager(connected("a", cleanup=cleanup), connected("b"),
                                         tools=[tool("mcp__a__x"), tool("mcp__b__y")]),
                         cwd=".", on_change=lambda: changes.append(1))
    session.dynamic_clients = [connected("a")]
    session.dynamic_tools = [tool("mcp__a__z"), tool("mcp__d__w")]
    asyncio.run(session.clear_server("a", STDIO))
    assert cleanup.calls == 1 and changes == [1]
    assert session.manager.clients[0] == FailedMCPServer(name="a", config=STDIO)
    assert session.manager.clients[1].type == "connected"
    assert session.dynamic_clients == [FailedMCPServer(name="a", config=STDIO)]
    assert [t.name for t in session.manager.tools] == ["mcp__b__y"]
    assert [t.name for t in session.dynamic_tools] == ["mcp__d__w"]
