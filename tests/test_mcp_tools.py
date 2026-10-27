"""
`app/tools/mcp_tool/mcp_tool.py`: MCP tools as loop tools, the resource tools, and how the real
permission gate treats them (Phase 8).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from mcp.client._memory import InMemoryTransport

import app.tools.mcp_tool.mcp_tool as mt
from app.services.mcp.client import connect_to_server
from app.services.mcp.types import ConnectedMCPServer, FailedMCPServer, McpServerConfig
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, tool_context_for
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    ToolPermissionContext,
)
from app.utils.permissions.permissions import has_permissions_to_use_tool
from tests.test_mcp_client import demo_server


def ns(**kw: Any) -> Any:
    """A context double; the tools read only the attributes given."""
    return SimpleNamespace(**kw)


CONFIG = McpServerConfig(type="stdio", scope="project", command="unused")


def definition(name: str = "add", description: str | None = "Add.", annotations: Any = None,
               schema: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(name=name, description=description, annotations=annotations,
                           input_schema=schema if schema is not None else {"type": "object", "properties": {}})


def client(name: str = "demo", session: Any = None, resources: bool = True) -> ConnectedMCPServer:
    caps = SimpleNamespace(tools=object(), resources=object() if resources else None)
    return ConnectedMCPServer(name=name, config=CONFIG, session=session, capabilities=caps)


def test_the_wrapper_is_named_and_classified_as_upstreams():
    hints = SimpleNamespace(read_only_hint=True, destructive_hint=True)
    tool = mt.build_mcp_tool(client("my server"), definition("do it", annotations=hints))
    assert tool.name == "mcp__my_server__do_it"
    assert tool.server_name == "my server" and tool.mcp_tool_name == "do it"
    assert tool.is_mcp is True
    assert tool.is_read_only({}) and tool.is_concurrency_safe({}) and tool.is_destructive({})
    assert tool.is_implementation_writer() is False and tool.is_test_writer() is False
    assert tool.max_result_size_chars == 100_000
    assert tool.prompt == "Add." and tool.description({}) == "Add."
    assert tool.input_schema == {"type": "object", "properties": {}}


def test_absent_hints_are_false_and_absent_description_empty():
    tool = mt.build_mcp_tool(client(), definition(description=None, annotations=SimpleNamespace(
        read_only_hint=None, destructive_hint=None)))
    assert not tool.is_read_only({}) and not tool.is_concurrency_safe({}) and not tool.is_destructive({})
    assert tool.prompt == "" and tool.description({}) == ""
    bare = mt.build_mcp_tool(client(), definition(annotations=None, schema={}))
    assert not bare.is_read_only({}) and bare.input_schema == {"type": "object"}


def test_a_long_description_is_capped_for_the_prompt_only():
    tool = mt.build_mcp_tool(client(), definition(description="d" * 3000))
    assert tool.prompt == "d" * 2048 + "… [truncated]"
    assert tool.description({}) == "d" * 3000


def test_permissions_are_passthrough():
    tool = mt.build_mcp_tool(client(), definition())
    result: Any = tool._check_permissions({}, ns())
    assert result.behavior == "passthrough" and result.message == "MCPTool requires permission."


def test_classifier_input():
    tool = mt.build_mcp_tool(client(), definition("search"))
    assert tool.to_auto_classifier_input({}) == "search"
    assert tool.to_auto_classifier_input({"q": "x", "n": 2, "f": True, "z": None, "l": [1, "a"], "o": {}}) == (
        "q=x n=2 f=true z=null l=1,a o=[object Object]")
    bare = mt.McpBuiltTool(name="n", prompt="", _call=lambda a, c: ToolResult(content=""))
    assert bare.to_auto_classifier_input({"a": 1}) == ""


class RecordingSession:
    def __init__(self, dump: dict[str, Any]) -> None:
        self.dump = dump
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, args: dict[str, Any], **_: Any) -> Any:
        self.calls.append((name, args))
        return SimpleNamespace(model_dump=lambda **__: self.dump)


def test_each_wrapper_calls_its_own_tool():
    """The closure fix: tools built in one loop do not all call the last one."""
    session = RecordingSession({"content": [{"type": "text", "text": "ok"}]})
    conn = client(session=session)
    built = [mt.build_mcp_tool(conn, definition(n)) for n in ("a", "b")]
    ctx = ns(run_id="r", workspace=None, cancel=None)
    for tool in built:
        asyncio.run(tool.call({"x": 1}, ctx))
    assert session.calls == [("a", {"x": 1}), ("b", {"x": 1})]


def test_text_result_and_meta():
    session = RecordingSession({"content": [], "structuredContent": {"v": 1}, "_meta": {"m": 2}})
    tool = mt.build_mcp_tool(client(session=session), definition())
    result = asyncio.run(tool.call({}, ns(run_id="", workspace=SimpleNamespace(root="/w"))))
    assert result.content == '{"v":1}'
    assert result.metadata == {"mcp_meta": {"_meta": {"m": 2}, "structuredContent": {"v": 1}}}
    plain = mt.build_mcp_tool(client(session=RecordingSession({"toolResult": "t"})), definition())
    assert asyncio.run(plain.call({}, ns())).metadata is None


def test_block_results_reach_the_model_as_blocks():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}}
    session = RecordingSession({"content": [{"type": "text", "text": "look"},
                                            {"type": "image", "data": "aGk=", "mimeType": "image/png"}]})
    tool = mt.build_mcp_tool(client(session=session), definition())
    result = asyncio.run(tool.call({}, ns()))
    assert result.content == "look\n[image]"
    assert result.metadata == {"content_blocks": [{"type": "text", "text": "look"}, image]}
    message = tool.map_result(result, "tu")
    assert isinstance(message, ToolMessage) and message.tool_call_id == "tu"
    assert message.content == [{"type": "text", "text": "look"},
                               {"type": "image", "base64": "aGk=", "mime_type": "image/png"}]


def test_a_replaced_string_wins_over_the_blocks():
    blocks = [{"type": "text", "text": "a"}]
    replaced = ToolResult(content="hook says", metadata={"content_blocks": blocks})
    assert mt.map_mcp_result(replaced, "tu").content == "hook says"
    kept = ToolResult(content="a", metadata={"content_blocks": blocks})
    assert mt.map_mcp_result(kept, "tu").content == [{"type": "text", "text": "a"}]
    assert mt.map_mcp_result(ToolResult(content="p"), "tu").content == "p"


def test_render_blocks_as_text():
    assert mt.render_blocks_as_text([{"type": "text"}, {"type": "other"}, {"type": "image", "source": {}}]) == (
        "\n[image]")
    assert mt._to_langchain_block({"type": "image"}) == {"type": "image", "base64": "", "mime_type": "image/png"}


def test_a_server_error_raises_to_the_pipeline():
    session = RecordingSession({"isError": True, "content": [{"type": "text", "text": "nope"}]})
    tool = mt.build_mcp_tool(client(session=session), definition())
    with pytest.raises(Exception, match="nope"):
        asyncio.run(tool.call({}, ns()))


# --- the real gate --------------------------------------------------------------------------

def gate(tool: Any, *, allow: tuple[str, ...] = (), phase: TddPhase = TddPhase.RED,
         mode: PermissionMode = PermissionMode.DEFAULT) -> Any:
    perm = ToolPermissionContext(
        mode=mode,
        always_allow_rules=tuple(PermissionRule(tool_name=a) for a in allow),
        should_avoid_permission_prompts=True,
    )
    store = AppStateStore(state=AppState(phase_ledger=PhaseLedger(phase=phase), tool_permission_context=perm))
    ctx = tool_context_for(store, workspace=SimpleNamespace(root="/w"))
    return asyncio.run(has_permissions_to_use_tool(tool, {}, ctx, "tu"))


def test_headless_without_an_allow_rule_is_denied():
    result = gate(mt.build_mcp_tool(client(), definition()))
    assert result.behavior == PermissionBehavior.DENY


def test_an_acceptedits_run_still_denies_an_unlisted_mcp_tool():
    result = gate(mt.build_mcp_tool(client(), definition()), mode=PermissionMode.ACCEPT_EDITS)
    assert result.behavior == PermissionBehavior.DENY


@pytest.mark.parametrize("rule", ["mcp__demo__add", "mcp__demo"])
def test_a_tool_or_server_allow_rule_allows(rule):
    result = gate(mt.build_mcp_tool(client(), definition()), allow=(rule,))
    assert result.behavior == PermissionBehavior.ALLOW


def test_another_servers_rule_does_not_allow():
    result = gate(mt.build_mcp_tool(client(), definition()), allow=("mcp__other",))
    assert result.behavior == PermissionBehavior.DENY


def test_the_tdd_phase_tier_runs_before_the_allow_rule(monkeypatch):
    import app.tdd.phase_permissions as tdd

    seen: list[str] = []

    def refuse(tool: Any, args: Any, ledger: Any) -> tuple[bool, str]:
        seen.append(tool.name)
        return False, "phase forbids"

    monkeypatch.setattr(tdd, "check_tdd_phase_permission", refuse)
    result = gate(mt.build_mcp_tool(client(), definition()), allow=("mcp__demo",))
    assert result.behavior == PermissionBehavior.DENY
    assert result.decision_reason["type"] == "phase_rule" and result.message == "phase forbids"
    assert seen == ["mcp__demo__add"]


# --- resource tools -------------------------------------------------------------------------

def test_resource_tool_surfaces():
    listing, reading = mt.build_list_mcp_resources_tool(), mt.build_read_mcp_resource_tool()
    assert listing.name == "ListMcpResourcesTool" and reading.name == "ReadMcpResourceTool"
    for tool in (listing, reading):
        assert tool.is_read_only({}) and tool.is_concurrency_safe({}) and not tool.is_mcp
        assert tool.max_result_size_chars == 100_000
    assert listing.prompt.startswith("List available resources from configured MCP servers.")
    assert reading.prompt.startswith("Reads a specific resource from an MCP server, identified by server name")
    assert reading.input_schema["required"] == ["server", "uri"]
    assert "required" not in listing.input_schema
    assert listing.to_auto_classifier_input({}) == "" and listing.to_auto_classifier_input({"server": "s"}) == "s"
    assert reading.to_auto_classifier_input({"server": "s", "uri": "u"}) == "s u"


def with_demo(scenario: Any) -> Any:
    async def outer() -> Any:
        conn = await connect_to_server("demo", CONFIG, cwd="/w",
                                       transport_factory=lambda n, c: InMemoryTransport(demo_server()))
        try:
            return await scenario(conn)
        finally:
            assert isinstance(conn, ConnectedMCPServer) and conn.cleanup is not None
            await conn.cleanup()

    return asyncio.run(outer())


def test_list_resources_from_all_or_one_server():
    listing = mt.build_list_mcp_resources_tool()
    failed = FailedMCPServer(name="down", config=CONFIG)

    async def scenario(conn: Any) -> Any:
        ctx = ns(mcp_clients=(conn, failed))
        return (await listing.call({}, ctx), await listing.call({"server": "down"}, ctx))

    every, none = with_demo(scenario)
    assert json.loads(every.content)[0] == {"uri": "mem://greeting", "name": "greeting", "mimeType": "text/plain",
                                            "server": "demo"}
    assert none.content == mt.NO_RESOURCES_MESSAGE


def test_list_resources_unknown_server():
    ctx = ns(mcp_clients=(FailedMCPServer(name="a", config=CONFIG), client("b")))
    with pytest.raises(ValueError, match='Server "x" not found. Available servers: a, b'):
        asyncio.run(mt.build_list_mcp_resources_tool().call({"server": "x"}, ctx))
    empty = asyncio.run(mt.build_list_mcp_resources_tool().call({}, ns()))
    assert empty.content == mt.NO_RESOURCES_MESSAGE


def test_read_resource_tool():
    reading = mt.build_read_mcp_resource_tool()

    async def scenario(conn: Any) -> Any:
        return await reading.call({"server": "demo", "uri": "mem://greeting"},
                                  ns(mcp_clients=(conn,), run_id="r"))

    result = with_demo(scenario)
    assert result.content == '{"contents":[{"uri":"mem://greeting","mimeType":"text/plain","text":"hello"}]}'


@pytest.mark.parametrize(("clients", "message"), [
    ((), 'Server "s" not found. Available servers: '),
    ((FailedMCPServer(name="s", config=CONFIG),), 'Server "s" is not connected'),
    ((client("s", resources=False),), 'Server "s" does not support resources'),
])
def test_read_resource_refusals(clients, message):
    ctx = ns(mcp_clients=clients)
    with pytest.raises(ValueError) as info:
        asyncio.run(mt.build_read_mcp_resource_tool().call({"server": "s", "uri": "u"}, ctx))
    assert str(info.value) == message


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_the_resource_tools_schemas_are_upstreams():
    """`ListMcpResourcesTool` / `ReadMcpResourceTool` `inputSchema`, as the model receives them."""
    assert mt.build_list_mcp_resources_tool().input_schema == {
        "type": "object",
        "properties": {"server": {"type": "string", "description": "Optional server name to filter resources by"}},
    }
    assert mt.build_read_mcp_resource_tool().input_schema == {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "The MCP server name"},
            "uri": {"type": "string", "description": "The resource URI to read"},
        },
        "required": ["server", "uri"],
    }


def test_resource_results_are_compact_json_with_unicode_kept(monkeypatch):
    """`jsonStringify`: no spaces, non-ASCII as is."""
    from app.services.mcp.types import ServerResource

    async def fetch(_client: Any) -> list[ServerResource]:
        return [ServerResource(uri="u", name="café", server="s")]

    seen: dict[str, Any] = {}

    async def read(_client: Any, uri: str, *, run_id: str, cwd: str) -> dict[str, Any]:
        seen.update(uri=uri, run_id=run_id, cwd=cwd)
        return {"contents": [{"uri": uri, "text": "é"}]}

    monkeypatch.setattr(mt, "fetch_resources_for_client", fetch)
    monkeypatch.setattr(mt, "read_resource", read)
    listed = asyncio.run(mt.build_list_mcp_resources_tool().call({}, ns(mcp_clients=(client("s"),))))
    assert listed.content == '[{"uri":"u","name":"café","server":"s"}]'
    ctx = ns(mcp_clients=(client("s"),), run_id="r9", workspace=SimpleNamespace(root="/ws"))
    read_out = asyncio.run(mt.build_read_mcp_resource_tool().call({"server": "s", "uri": "x://1"}, ctx))
    assert read_out.content == '{"contents":[{"uri":"x://1","text":"é"}]}'
    assert seen == {"uri": "x://1", "run_id": "r9", "cwd": "/ws"}
    asyncio.run(mt.build_read_mcp_resource_tool().call({"server": "s", "uri": "x://1"}, ns(mcp_clients=(client("s"),))))
    assert (seen["run_id"], seen["cwd"]) == ("run", ".")


def test_a_server_without_a_resources_capability_is_refused():
    bare = ConnectedMCPServer(name="s", config=CONFIG, session=None, capabilities=SimpleNamespace())
    with pytest.raises(ValueError, match='Server "s" does not support resources'):
        asyncio.run(mt.build_read_mcp_resource_tool().call({"server": "s", "uri": "u"}, ns(mcp_clients=(bare,))))


def test_the_call_gets_the_runs_id_cwd_and_cancel_token(monkeypatch):
    seen: list[dict[str, Any]] = []

    async def fake_call(_client: Any, name: str, args: Any, **kw: Any) -> dict[str, Any]:
        seen.append(kw)
        return {"content": None}

    monkeypatch.setattr(mt, "call_mcp_tool", fake_call)
    tool = mt.build_mcp_tool(client(), definition())
    token = object()
    out = asyncio.run(tool.call({}, ns(run_id="r1", workspace=SimpleNamespace(root="/ws"), cancel=token)))
    assert seen[0] == {"run_id": "r1", "cwd": "/ws", "cancel": token}
    assert out.content == "" and out.metadata is None
    asyncio.run(tool.call({}, ns()))
    assert seen[1] == {"run_id": "run", "cwd": ".", "cancel": None}


@pytest.mark.parametrize("key", ["_meta", "structuredContent"])
def test_either_meta_field_alone_is_kept(monkeypatch, key):
    async def fake_call(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"content": "t", key: {"v": 1}}

    monkeypatch.setattr(mt, "call_mcp_tool", fake_call)
    out = asyncio.run(mt.build_mcp_tool(client(), definition()).call({}, ns()))
    assert out.metadata == {"mcp_meta": {key: {"v": 1}}}


def test_classifier_input_renders_false_as_js():
    tool = mt.build_mcp_tool(client(), definition())
    assert tool.to_auto_classifier_input({"flag": False, "on": True}) == "flag=false on=true"


def test_an_image_keeps_its_media_type():
    block = {"type": "image", "source": {"data": "x", "media_type": "image/jpeg"}}
    assert mt._to_langchain_block(block) == {"type": "image", "base64": "x", "mime_type": "image/jpeg"}
