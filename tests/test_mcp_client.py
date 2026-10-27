"""
`app/services/mcp/client.py`: connecting, listing, calling and result transformation (Phase 8),
against an in-memory SDK server (`mcp.client._memory.InMemoryTransport`), never the network.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import EmbeddedResource, ImageContent, TextContent, TextResourceContents
from pydantic import BaseModel

import app.services.mcp.client as mc
from app.services.mcp.types import ConnectedMCPServer, FailedMCPServer, McpServerConfig

CONFIG = McpServerConfig(type="stdio", scope="project", command="unused")


def demo_server(instructions: str | None = "Use add.") -> MCPServer:
    srv = MCPServer("demo", instructions=instructions)

    @srv.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @srv.tool()
    def boom() -> str:
        raise ValueError("bad")

    @srv.tool(structured_output=False)
    def picture() -> list[Any]:
        return [TextContent(type="text", text="look"), ImageContent(type="image", data="aGk=", mime_type="image/png")]

    @srv.tool(structured_output=False)
    def embedded() -> list[Any]:
        return [EmbeddedResource(type="resource", resource=TextResourceContents(uri="mem://a", text="body"))]

    @srv.tool()
    def big() -> str:
        return "x" * 200_000

    @srv.resource("mem://greeting", name="greeting", mime_type="text/plain")
    def greeting() -> str:
        return "hello"

    @srv.resource("mem://blob", name="blob", mime_type="application/pdf")
    def blob() -> bytes:
        return b"%PDF"

    return srv


def factory(server: MCPServer) -> Any:
    return lambda _name, _config: InMemoryTransport(server)


async def close(conn: ConnectedMCPServer) -> None:
    assert conn.cleanup is not None
    await conn.cleanup()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def connected(server: MCPServer | None = None, **kw: Any) -> ConnectedMCPServer:
    conn = await mc.connect_to_server(
        "demo", CONFIG, cwd="/w", transport_factory=factory(server or demo_server()), **kw
    )
    assert isinstance(conn, ConnectedMCPServer)
    return conn


def test_connect_reports_instructions_capabilities_and_closes():
    async def scenario() -> None:
        conn = await connected()
        assert conn.name == "demo" and conn.config is CONFIG and conn.type == "connected"
        assert conn.instructions == "Use add."
        assert conn.capabilities.tools is not None and conn.capabilities.resources is not None
        assert conn.server_info.name == "demo"
        assert conn.cleanup is not None
        await close(conn)

    run(scenario())


def test_long_instructions_are_capped():
    async def scenario() -> None:
        conn = await connected(demo_server("i" * 3000))
        assert conn.instructions == "i" * 2048 + "… [truncated]"
        await close(conn)

    run(scenario())


def test_exactly_the_cap_is_kept_and_none_stays_none():
    assert mc.truncate_description("i" * 2048) == "i" * 2048

    async def scenario() -> None:
        conn = await connected(demo_server(None))
        assert conn.instructions is None
        await close(conn)

    run(scenario())


def test_a_transport_that_raises_is_a_failed_server():
    def broken(_name: str, _config: McpServerConfig) -> Any:
        raise OSError("spawn failed")

    async def scenario() -> Any:
        return await mc.connect_to_server("bad", CONFIG, cwd="/w", transport_factory=broken)

    failed = run(scenario())
    assert isinstance(failed, FailedMCPServer)
    assert failed.name == "bad" and failed.config is CONFIG and failed.error == "spawn failed"


def test_a_connection_that_hangs_times_out(monkeypatch):
    monkeypatch.setenv(mc.MCP_TIMEOUT_ENV, "50")

    class Hang:
        async def __aenter__(self) -> Any:
            await asyncio.sleep(10)

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def scenario() -> Any:
        return await mc.connect_to_server("slow", CONFIG, cwd="/w", transport_factory=lambda n, c: Hang())

    failed = run(scenario())
    assert isinstance(failed, FailedMCPServer)
    assert failed.error == 'MCP server "slow" connection timed out after 50ms'


@pytest.mark.parametrize(("raw", "expected"), [("", 30000), ("abc", 30000), ("0", 30000), ("75ms", 75), (" 12", 12)])
def test_timeouts_parse_like_parse_int(monkeypatch, raw, expected):
    monkeypatch.setenv(mc.MCP_TIMEOUT_ENV, raw)
    assert mc.get_connection_timeout_ms() == expected


def test_tool_timeout_defaults_to_upstreams(monkeypatch):
    monkeypatch.delenv(mc.MCP_TOOL_TIMEOUT_ENV, raising=False)
    assert mc.get_mcp_tool_timeout_ms() == 100_000_000
    monkeypatch.setenv(mc.MCP_TOOL_TIMEOUT_ENV, "7")
    assert mc.get_mcp_tool_timeout_ms() == 7


def test_fetch_tools_and_resources():
    async def scenario() -> tuple[list[Any], list[Any]]:
        conn = await connected()
        try:
            return await mc.fetch_tools_for_client(conn), await mc.fetch_resources_for_client(conn)
        finally:
            await close(conn)

    tools, resources = run(scenario())
    assert [t.name for t in tools] == ["add", "boom", "picture", "embedded", "big"]
    assert resources[0].as_dict() == {"uri": "mem://greeting", "name": "greeting", "mimeType": "text/plain",
                                      "server": "demo"}


def test_fetch_is_empty_without_the_capability_or_a_connection():
    no_caps = ConnectedMCPServer(name="n", config=CONFIG, session=None, capabilities=SimpleNamespace())
    failed = FailedMCPServer(name="f", config=CONFIG)
    assert run(mc.fetch_tools_for_client(no_caps)) == []
    assert run(mc.fetch_resources_for_client(no_caps)) == []
    assert run(mc.fetch_tools_for_client(failed)) == []
    assert run(mc.fetch_resources_for_client(failed)) == []


def test_fetch_failures_are_empty():
    class Boom:
        async def list_tools(self) -> Any:
            raise RuntimeError("x")

        async def list_resources(self) -> Any:
            raise RuntimeError("x")

    caps = SimpleNamespace(tools=object(), resources=object())
    conn = ConnectedMCPServer(name="n", config=CONFIG, session=Boom(), capabilities=caps)
    assert run(mc.fetch_tools_for_client(conn)) == []
    assert run(mc.fetch_resources_for_client(conn)) == []


def call(tool: str, args: dict[str, Any], tmp_path: Any, **kw: Any) -> Any:
    async def scenario() -> Any:
        conn = await connected()
        try:
            return await mc.call_mcp_tool(conn, tool, args, run_id="r1", cwd=str(tmp_path), **kw)
        finally:
            await close(conn)

    return run(scenario())


def test_structured_content_wins_over_content(tmp_path):
    out = call("add", {"a": 2, "b": 3}, tmp_path)
    assert out == {"content": '{"result":5}', "structuredContent": {"result": 5}}


def test_is_error_raises_with_the_first_text(tmp_path):
    with pytest.raises(mc.McpToolCallError, match="Error executing tool boom"):
        call("boom", {}, tmp_path)


def test_content_array_with_an_image(tmp_path):
    out = call("picture", {}, tmp_path)
    assert out["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGk="}},
    ]


def test_an_embedded_text_resource_is_prefixed(tmp_path):
    expected = [{"type": "text", "text": "[Resource from demo at mem://a] body"}]
    assert call("embedded", {}, tmp_path)["content"] == expected


def test_large_text_is_saved_to_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    out = call("big", {}, tmp_path)
    [saved] = list((tmp_path / "cfg").rglob("mcp-demo-big-*.txt"))
    assert json.loads(saved.read_text()) == {"result": "x" * 200_000}
    assert out["content"].startswith(f"Error: result (200,013 characters) exceeds maximum allowed tokens. "
                                     f"Output has been saved to {saved}.\n")
    assert "Format: JSON with schema: {result: string}" in out["content"]


def test_large_output_files_disabled_truncates(tmp_path, monkeypatch):
    monkeypatch.setenv(mc.LARGE_OUTPUT_FILES_ENV, "false")
    out = call("big", {}, tmp_path)
    assert "[OUTPUT TRUNCATED - exceeded 25000 token limit]" in out["content"]


def test_a_call_that_hangs_times_out(tmp_path, monkeypatch):
    monkeypatch.setenv(mc.MCP_TOOL_TIMEOUT_ENV, "1000")

    class Slow:
        async def call_tool(self, *a: Any, **k: Any) -> Any:
            await asyncio.sleep(10)

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Slow(), capabilities=None)
    with pytest.raises(TimeoutError, match='MCP server "s" tool "t" timed out after 1s'):
        run(mc.call_mcp_tool(conn, "t", {}, run_id="r", cwd=str(tmp_path)))


def test_a_cancelled_context_abandons_the_call(tmp_path):
    started = []

    class Slow:
        async def call_tool(self, *a: Any, **k: Any) -> Any:
            started.append(True)
            await asyncio.sleep(10)

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Slow(), capabilities=None)
    token = SimpleNamespace(cancelled=False)

    async def scenario() -> None:
        task = asyncio.create_task(mc.call_mcp_tool(conn, "t", {}, run_id="r", cwd=str(tmp_path), cancel=token))
        await asyncio.sleep(0.01)
        token.cancelled = True
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

    run(scenario())
    assert started == [True]


def test_is_error_without_text_and_with_error_field(tmp_path):
    class Fake:
        def __init__(self, dump: dict[str, Any]) -> None:
            self.dump = dump

        async def call_tool(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(model_dump=lambda **_: self.dump)

    for dump, expected in (
        ({"isError": True, "content": [{"type": "image"}]}, "Unknown error"),
        ({"isError": True, "content": [], "error": "e"}, "e"),
        ({"isError": True}, "Unknown error"),
    ):
        conn = ConnectedMCPServer(name="s", config=CONFIG, session=Fake(dump), capabilities=None)
        with pytest.raises(mc.McpToolCallError) as info:
            run(mc.call_mcp_tool(conn, "t", {}, run_id="r", cwd=str(tmp_path)))
        assert str(info.value) == expected
    meta_conn = ConnectedMCPServer(
        name="s", config=CONFIG, session=Fake({"isError": True, "_meta": {"k": 1}}), capabilities=None
    )
    with pytest.raises(mc.McpToolCallError) as info:
        run(mc.call_mcp_tool(meta_conn, "t", {}, run_id="r", cwd=str(tmp_path)))
    assert info.value.meta == {"k": 1}


def test_meta_and_arguments_reach_the_session(tmp_path):
    seen: dict[str, Any] = {}

    class Fake:
        async def call_tool(self, name: str, args: dict[str, Any], **k: Any) -> Any:
            seen.update(name=name, args=args, **k)
            return SimpleNamespace(model_dump=lambda **_: {"content": [], "_meta": {"m": 1}})

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Fake(), capabilities=None)
    out = run(mc.call_mcp_tool(conn, "t", {"a": 1}, run_id="r", cwd=str(tmp_path), meta={"x": "y"}))
    assert out == {"content": [], "_meta": {"m": 1}}
    assert seen["name"] == "t" and seen["args"] == {"a": 1} and seen["meta"] == {"x": "y"}
    assert seen["read_timeout_seconds"] == 100_000


def test_transform_result_content_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    kw = {"run_id": "r", "cwd": "/w"}
    assert mc.transform_result_content({"type": "text", "text": "t"}, "s", **kw) == [{"type": "text", "text": "t"}]
    assert mc.transform_result_content({"type": "image", "data": "d"}, "s", **kw) == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "d"}}]
    assert mc.transform_result_content({"type": "image", "data": "d", "mimeType": "image/gif"}, "s", **kw)[0][
        "source"]["media_type"] == "image/gif"
    resource = {"type": "resource", "resource": {"uri": "u", "text": "body"}}
    assert mc.transform_result_content(resource, "s", **kw) == [{"type": "text", "text": "[Resource from s at u] body"}]
    image_blob = {"type": "resource", "resource": {"uri": "u", "blob": "aGk=", "mimeType": "image/webp"}}
    assert mc.transform_result_content(image_blob, "s", **kw) == [
        {"type": "text", "text": "[Resource from s at u] "},
        {"type": "image", "source": {"type": "base64", "media_type": "image/webp", "data": "aGk="}},
    ]
    pdf = {"type": "resource", "resource": {"uri": "u", "blob": base64.b64encode(b"%PDF").decode(),
                                            "mimeType": "application/pdf"}}
    [text] = mc.transform_result_content(pdf, "my srv", **kw)
    assert text["text"].startswith("[Resource from my srv at u] Binary content (application/pdf, 4 bytes) saved to ")
    saved = text["text"].rsplit(" saved to ", 1)[1]
    assert saved.endswith(".pdf") and "mcp-my_srv-blob-" in saved
    audio = mc.transform_result_content({"type": "audio", "data": "aGk=", "mimeType": "audio/wav"}, "s", **kw)
    assert audio[0]["text"].startswith("[Audio from s] Binary content (audio/wav, 2 bytes) saved to ")
    assert mc.transform_result_content({"type": "resource", "resource": {"uri": "u"}}, "s", **kw) == []
    link = {"type": "resource_link", "name": "n", "uri": "u"}
    assert mc.transform_result_content(link, "s", **kw) == [{"type": "text", "text": "[Resource link: n] u"}]
    link["description"] = "d"
    assert mc.transform_result_content(link, "s", **kw) == [{"type": "text", "text": "[Resource link: n] u (d)"}]
    assert mc.transform_result_content({"type": "other"}, "s", **kw) == []


def test_a_blob_that_cannot_be_saved_says_so(monkeypatch):
    def fail(*a: Any, **k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(mc, "persist_binary_content", fail)
    [block] = mc.transform_result_content({"type": "audio", "data": "aGk="}, "s", run_id="r", cwd="/w")
    assert block == {"type": "text", "text": "[Audio from s] Binary content (unknown type, 2 bytes) could not be "
                                             "saved to disk: disk full"}


def test_infer_compact_schema():
    assert mc.infer_compact_schema(None) == "null"
    assert mc.infer_compact_schema([]) == "[]"
    assert mc.infer_compact_schema([1]) == "[number]"
    assert mc.infer_compact_schema({"a": True, "b": "s", "c": 1.5, "d": {"e": 1}}) == (
        "{a: boolean, b: string, c: number, d: {e: number}}")
    assert mc.infer_compact_schema({"a": {"b": {"c": 1}}}) == "{a: {b: {...}}}"
    assert mc.infer_compact_schema({str(i): i for i in range(11)}).endswith("9: number, ...}")
    assert mc.infer_compact_schema({str(i): i for i in range(10)}).endswith("9: number}")
    assert mc.infer_compact_schema([[1]], 1) == "[[number]]"


def test_transform_mcp_result_branches():
    kw = {"run_id": "r", "cwd": "/w"}
    assert mc.transform_mcp_result({"toolResult": 3}, "t", "s", **kw) == ("3", "toolResult", None)
    assert mc.transform_mcp_result({"structuredContent": {"a": 1}, "content": []}, "t", "s", **kw) == (
        '{"a":1}', "structuredContent", "{a: number}")
    assert mc.transform_mcp_result({"structuredContent": None, "content": []}, "t", "s", **kw) == (
        [], "contentArray", "[]")
    with pytest.raises(ValueError, match='MCP server "s" tool "t": unexpected response format'):
        mc.transform_mcp_result({}, "t", "s", **kw)


def test_process_keeps_images_by_truncating(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    result = {"content": [{"type": "text", "text": "y" * 100}, {"type": "image", "data": "d"}]}
    out = mc.process_mcp_result(result, "t", "s", run_id="r", cwd="/w")
    assert isinstance(out, list) and out[-1]["text"].startswith("\n\n[OUTPUT TRUNCATED")


def test_process_passes_small_and_empty_results(monkeypatch):
    assert mc.process_mcp_result({"toolResult": "ok"}, "t", "s", run_id="r", cwd="/w") == "ok"
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "1")
    monkeypatch.setattr(mc, "mcp_content_needs_truncation", lambda c: True)
    assert mc.process_mcp_result({"toolResult": ""}, "t", "s", run_id="r", cwd="/w") == ""


def test_process_reports_a_failed_save(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    monkeypatch.setattr(mc, "tool_results_dir", lambda *a: tmp_path / "f" / "x")
    (tmp_path / "f").write_text("not a dir")
    out = mc.process_mcp_result({"toolResult": "z" * 100}, "t", "s", run_id="r", cwd="/w")
    assert isinstance(out, str)
    assert out.startswith("Error: result (100 characters) exceeds maximum allowed tokens. Failed to save output to "
                          "file: ")
    assert out.endswith("If this MCP server provides pagination or filtering tools, use them to retrieve specific "
                        "portions of the data.")


def test_process_saves_a_large_block_list_as_json(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    out = mc.process_mcp_result({"content": [{"type": "text", "text": "q" * 100}]}, "t", "s", run_id="r", cwd="/w")
    assert isinstance(out, str)
    assert "Format: JSON array with schema: [{type: string, text: string}]" in out
    [saved] = list(tmp_path.rglob("mcp-s-t-*.json"))
    assert json.loads(saved.read_text()) == [{"type": "text", "text": "q" * 100}]


def test_read_resource_text_and_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))

    async def scenario() -> tuple[Any, Any]:
        conn = await connected()
        try:
            return (await mc.read_resource(conn, "mem://greeting", run_id="r", cwd="/w"),
                    await mc.read_resource(conn, "mem://blob", run_id="r", cwd="/w"))
        finally:
            await close(conn)

    text, blob = run(scenario())
    assert text == {"contents": [{"uri": "mem://greeting", "mimeType": "text/plain", "text": "hello"}]}
    [entry] = blob["contents"]
    assert entry["uri"] == "mem://blob" and entry["mimeType"] == "application/pdf"
    assert entry["blobSavedTo"].endswith(".pdf")
    assert entry["text"] == (f"[Resource from demo at mem://blob] Binary content (application/pdf, 4 bytes) saved to "
                             f"{entry['blobSavedTo']}")


def test_read_resource_blob_save_failure(monkeypatch):
    def fail(*a: Any, **k: Any) -> Any:
        raise OSError("nope")

    monkeypatch.setattr(mc, "persist_binary_content", fail)

    class Fake:
        async def read_resource(self, uri: str) -> Any:
            dump = {"contents": [{"uri": uri, "blob": "aGk="}, {"uri": uri}]}
            return SimpleNamespace(model_dump=lambda **_: dump)

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Fake(), capabilities=None)
    assert run(mc.read_resource(conn, "u", run_id="r", cwd="/w")) == {"contents": [
        {"uri": "u", "text": "Binary content could not be saved to disk: nope"}, {"uri": "u"}]}


def test_roots_and_elicitation_callbacks():
    roots = run(mc._roots_callback("/w")(None))  # type: ignore[arg-type]
    assert [str(r.uri) for r in roots.roots] == ["file:///w"]
    assert run(mc._cancel_elicitation(None, None)).action == "cancel"  # type: ignore[arg-type]


def test_get_mcp_instructions():
    a = ConnectedMCPServer(name="a", config=CONFIG, session=None, capabilities=None, instructions="do a")
    b = ConnectedMCPServer(name="b", config=CONFIG, session=None, capabilities=None)
    c = FailedMCPServer(name="c", config=CONFIG)
    assert mc.get_mcp_instructions([b, c]) is None
    assert mc.get_mcp_instructions([a, b, c, a]) == (
        "# MCP Server Instructions\n\nThe following MCP servers have provided instructions for how to use their "
        "tools and resources:\n\n## a\ndo a\n\n## a\ndo a")


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def _frozen(monkeypatch: Any) -> None:
    """A fixed clock (1.5 s) and token, so persisted names are exact."""
    monkeypatch.setattr("app.services.mcp.client.time.time", lambda: 1.5)
    monkeypatch.setattr("app.services.mcp.client.secrets.token_hex", lambda n: "ab" * n)


def test_blobs_are_saved_under_the_runs_tool_results_with_upstreams_name(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import tool_results_dir

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    _frozen(monkeypatch)
    kw = {"run_id": "r7", "cwd": "/w7"}
    audio = mc.transform_result_content({"type": "audio", "data": "aGk=", "mimeType": "audio/wav"}, "my srv", **kw)
    path = tool_results_dir("r7", "/w7") / "mcp-my_srv-blob-1500-ababab.wav"
    expected = f"[Audio from my srv] Binary content (audio/wav, 2 bytes) saved to {path}"
    assert audio == [{"type": "text", "text": expected}]
    pdf = {"type": "resource", "resource": {"uri": "u", "blob": "aGk=", "mimeType": "application/pdf"}}
    [block] = mc.transform_result_content(pdf, "s", **kw)
    assert block["text"].endswith(f"saved to {tool_results_dir('r7', '/w7') / 'mcp-s-blob-1500-ababab.pdf'}")
    assert path.read_bytes() == b"hi"


def test_a_processed_result_saves_its_blobs_under_the_run(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import tool_results_dir

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    _frozen(monkeypatch)
    out = mc.process_mcp_result({"content": [{"type": "audio", "data": "aGk="}]}, "t", "s", run_id="r8", cwd="/w8")
    assert isinstance(out, list) and str(tool_results_dir("r8", "/w8")) in out[0]["text"]
    with pytest.raises(ValueError, match='MCP server "s" tool "t": unexpected response format'):
        mc.process_mcp_result({}, "t", "s", run_id="r8", cwd="/w8")


def test_a_large_result_is_saved_as_indented_json_under_the_run(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import tool_results_dir

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    _frozen(monkeypatch)
    blocks = [{"type": "text", "text": "é" * 100}]
    out = mc.process_mcp_result({"content": blocks}, "my tool", "my srv", run_id="r9", cwd="/w9")
    saved = tool_results_dir("r9", "/w9") / "mcp-my_srv-my_tool-1500.json"
    assert saved.read_text(encoding="utf-8") == json.dumps(blocks, ensure_ascii=False, indent=2)
    assert isinstance(out, str) and f"Output has been saved to {saved}." in out
    # The directory may already exist: a second save beside the first.
    mc.process_mcp_result({"content": blocks}, "my tool", "my srv", run_id="r9", cwd="/w9")


def test_a_called_tools_large_output_lands_under_the_calls_cwd(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import tool_results_dir

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    call("big", {}, tmp_path)
    assert list(tool_results_dir("r1", str(tmp_path)).glob("mcp-demo-big-*.txt"))


def test_a_read_resource_blob_lands_under_the_run(tmp_path, monkeypatch):
    from app.utils.tool_result_storage import tool_results_dir

    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    _frozen(monkeypatch)

    async def scenario() -> Any:
        conn = await connected()
        try:
            return await mc.read_resource(conn, "mem://blob", run_id="r", cwd="/w")
        finally:
            await close(conn)

    [entry] = run(scenario())["contents"]
    assert entry["blobSavedTo"] == str(tool_results_dir("r", "/w") / "mcp-resource-1500-0-ababab.pdf")


@pytest.mark.parametrize("value", ["0", "no", "off", " OFF "])
def test_every_falsy_spelling_disables_large_output_files(monkeypatch, value):
    monkeypatch.setenv(mc.LARGE_OUTPUT_FILES_ENV, value)
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    out = mc.process_mcp_result({"toolResult": "z" * 100}, "t", "s", run_id="r", cwd="/w")
    assert "[OUTPUT TRUNCATED" in str(out)


def test_missing_fields_default_to_empty():
    kw = {"run_id": "r", "cwd": "/w"}
    assert mc.transform_result_content({"type": "text"}, "s", **kw) == [{"type": "text", "text": ""}]
    assert mc.transform_result_content({"type": "image"}, "s", **kw)[0]["source"]["data"] == ""


def test_audio_without_data_is_zero_bytes(monkeypatch):
    def fail(data: bytes, *a: Any, **k: Any) -> Any:
        raise OSError(f"{len(data)} bytes")

    monkeypatch.setattr(mc, "persist_binary_content", fail)
    [block] = mc.transform_result_content({"type": "audio"}, "s", run_id="r", cwd="/w")
    assert block["text"].endswith("(unknown type, 0 bytes) could not be saved to disk: 0 bytes")


def test_compact_json_and_schema_depth():
    assert mc.transform_mcp_result({"structuredContent": {"a": "é", "b": 2}}, "t", "s", run_id="r", cwd="/w")[0] == (
        '{"a":"é","b":2}')
    assert mc.infer_compact_schema([{"a": 1}], 1) == "[{...}]"
    assert mc.infer_compact_schema(object()) == "object"


def test_a_resources_description_is_kept():
    class Fake:
        async def list_resources(self) -> Any:
            return SimpleNamespace(resources=[SimpleNamespace(uri="u", name="n", mime_type=None, description="d")])

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Fake(), capabilities=SimpleNamespace(resources=True))
    [resource] = run(mc.fetch_resources_for_client(conn))
    assert resource.description == "d"


def test_a_timed_out_call_cancels_the_servers_request(tmp_path, monkeypatch):
    monkeypatch.setenv(mc.MCP_TOOL_TIMEOUT_ENV, "100")
    cancelled: list[bool] = []

    class Slow:
        async def call_tool(self, *a: Any, **k: Any) -> Any:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Slow(), capabilities=None)

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await mc.call_mcp_tool(conn, "t", {}, run_id="r", cwd=str(tmp_path))
        await asyncio.sleep(0)
        assert cancelled == [True]

    run(scenario())


def test_an_aborted_call_says_so(tmp_path):
    class Slow:
        async def call_tool(self, *a: Any, **k: Any) -> Any:
            await asyncio.sleep(10)

    conn = ConnectedMCPServer(name="s", config=CONFIG, session=Slow(), capabilities=None)
    token = SimpleNamespace(cancelled=True)
    with pytest.raises(asyncio.CancelledError, match="^MCP tool call aborted$"):
        run(mc.call_mcp_tool(conn, "t", {}, run_id="r", cwd=str(tmp_path), cancel=token))


def _holders(name: str) -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t.get_name() == f"mcp:{name}"]


def test_the_connection_task_is_awaited_on_every_exit(monkeypatch):
    """The holder task (named `mcp:<server>`) is finished before connect or cleanup returns."""
    monkeypatch.setenv(mc.MCP_TIMEOUT_ENV, "50")

    class Hang:
        async def __aenter__(self) -> Any:
            await asyncio.sleep(10)

        async def __aexit__(self, *exc: object) -> None:
            return None

    def broken(_name: str, _config: McpServerConfig) -> Any:
        raise OSError("spawn failed")

    async def scenario() -> None:
        slow = await mc.connect_to_server("slow", CONFIG, cwd="/w", transport_factory=lambda n, c: Hang())
        assert isinstance(slow, FailedMCPServer) and (slow.name, slow.config) == ("slow", CONFIG)
        assert _holders("slow") == []
        await mc.connect_to_server("bad", CONFIG, cwd="/w", transport_factory=broken)
        assert _holders("bad") == []
        conn = await connected()
        assert len(_holders("demo")) == 1
        await close(conn)
        assert _holders("demo") == []

    run(scenario())


def test_the_client_introduces_itself_as_tddagents(monkeypatch):
    from mcp import ClientSession

    seen: dict[str, Any] = {}

    def spy(*a: Any, **k: Any) -> Any:
        seen.update(k)
        return ClientSession(*a, **k)

    monkeypatch.setattr("app.services.mcp.client.ClientSession", spy)

    async def scenario() -> None:
        await close(await connected())

    run(scenario())
    info = seen["client_info"]
    assert (info.name, info.title) == ("tddagents", "TDDAgents")


class Answer(BaseModel):
    """An elicitation schema for the probe server."""

    value: str


def test_the_server_can_ask_for_roots_and_is_told_cancel_on_elicitation():
    """`connectToServer` registers the roots and (non-interactive) elicitation handlers."""
    srv = MCPServer("probe")

    @srv.tool()
    async def roots(ctx: Context) -> str:
        result = await ctx.request_context.session.list_roots()
        return ",".join(str(r.uri) for r in result.roots)

    @srv.tool()
    async def ask(ctx: Context) -> str:
        return (await ctx.elicit("name?", Answer)).action

    async def scenario() -> tuple[Any, Any]:
        conn = await connected(srv)
        try:
            return (await mc.call_mcp_tool(conn, "roots", {}, run_id="r", cwd="/w"),
                    await mc.call_mcp_tool(conn, "ask", {}, run_id="r", cwd="/w"))
        finally:
            await close(conn)

    listed, asked = run(scenario())
    assert listed["structuredContent"] == {"result": "file:///w"}
    assert asked["structuredContent"] == {"result": "cancel"}


def test_a_second_large_result_is_saved_beside_the_first(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TDDAGENTS_MAX_MCP_OUTPUT_TOKENS", "10")
    first = mc.process_mcp_result({"toolResult": "z" * 100}, "a", "s", run_id="r", cwd="/w")
    second = mc.process_mcp_result({"toolResult": "z" * 100}, "b", "s", run_id="r", cwd="/w")
    assert "Output has been saved to" in str(first) and "Output has been saved to" in str(second)
