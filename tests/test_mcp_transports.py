"""`app/loop/mcp/transports.py`: the real stdio and ws transports, headers, the factory (offline)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

import app.loop.mcp.transports as mtr
from app.loop.mcp.client import call_mcp_tool, connect_to_server
from app.loop.mcp.types import ConnectedMCPServer, FailedMCPServer, McpServerConfig

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py")


def stdio(*args: str, env: dict[str, str] | None = None) -> McpServerConfig:
    return McpServerConfig(type="stdio", scope="project", command=sys.executable, args=(SERVER, *args), env=env)


def test_stdio_server_round_trip_with_the_full_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TDDAGENTS_PARENT_ONLY", "inherited")

    async def scenario() -> tuple[Any, Any, Any]:
        conn = await connect_to_server("s", stdio(env={"DEMO_INSTRUCTIONS": "hi"}), cwd=str(tmp_path))
        assert isinstance(conn, ConnectedMCPServer) and conn.cleanup is not None
        try:
            echo = await call_mcp_tool(conn, "echo", {"text": "yo"}, run_id="r", cwd=str(tmp_path))
            env = await call_mcp_tool(conn, "env", {"name": "TDDAGENTS_PARENT_ONLY"}, run_id="r", cwd=str(tmp_path))
            return conn.instructions, echo["content"], env["content"]
        finally:
            await conn.cleanup()

    instructions, echo, env = asyncio.run(scenario())
    assert instructions == "hi"
    assert echo == '{"result":"yo"}' and env == '{"result":"inherited"}'


def test_a_crashing_stdio_server_fails_and_logs_its_stderr(tmp_path, caplog):
    async def scenario() -> Any:
        return await connect_to_server("s", stdio("--fail"), cwd=str(tmp_path))

    with caplog.at_level("WARNING"):
        failed = asyncio.run(scenario())
    assert isinstance(failed, FailedMCPServer)
    assert "MCP server s stderr: boom on stderr" in caplog.text


def test_an_unsupported_type_raises():
    with pytest.raises(ValueError, match="Unsupported server type: sse-ide"):
        mtr.open_transport("x", McpServerConfig(type="sse-ide", scope="user", url="u"))


def test_user_agent_and_version():
    assert mtr.get_mcp_user_agent() == f"tddagents/{mtr.client_version()}"
    assert mtr.client_version()


def helper_config(script: str) -> McpServerConfig:
    return McpServerConfig(type="http", scope="user", url="http://u", headers={"A": "static", "B": "static"},
                           headers_helper=script)


def test_headers_helper_overrides_static_headers():
    config = helper_config(
        "python3 -c 'import json,os; print(json.dumps({\"B\": \"dyn\", "
        "\"N\": os.environ[\"TDDAGENTS_MCP_SERVER_NAME\"], \"U\": os.environ[\"TDDAGENTS_MCP_SERVER_URL\"]}))'")
    assert mtr.get_mcp_server_headers("srv", config) == {"A": "static", "B": "dyn", "N": "srv", "U": "http://u"}


@pytest.mark.parametrize("script", [
    "exit 1",
    "true",
    "echo '[1]'",
    "echo '{\"k\": 1}'",
    "echo not-json",
])
def test_a_bad_headers_helper_is_ignored(script):
    config = helper_config(script)
    assert mtr.get_mcp_headers_from_helper("srv", config) is None
    assert mtr.get_mcp_server_headers("srv", config) == {"A": "static", "B": "static"}


def test_no_helper_no_headers():
    config = McpServerConfig(type="http", scope="user", url="http://u")
    assert mtr.get_mcp_headers_from_helper("srv", config) is None
    assert mtr.get_mcp_server_headers("srv", config) == {}


def test_websocket_transport_round_trip(tmp_path):
    """The ws transport against a loopback websockets server bridged to an in-process MCP server."""
    import anyio
    from mcp.shared.message import SessionMessage
    from mcp.types import jsonrpc_message_adapter
    from websockets.asyncio.server import serve

    from tests.test_mcp_client import demo_server

    srv = demo_server()
    seen: dict[str, Any] = {}

    async def handler(ws: Any) -> None:
        seen["subprotocol"] = ws.subprotocol
        seen["agent"] = ws.request.headers.get("User-Agent")
        to_server_w, to_server_r = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        from_server_w, from_server_r = anyio.create_memory_object_stream[SessionMessage](0)

        async def pump_in() -> None:
            async with to_server_w:
                async for raw in ws:
                    await to_server_w.send(SessionMessage(jsonrpc_message_adapter.validate_json(raw, by_name=False)))

        async def pump_out() -> None:
            async with from_server_r:
                async for msg in from_server_r:
                    await ws.send(msg.message.model_dump_json(by_alias=True, exclude_unset=True))

        lowlevel = srv._lowlevel_server
        async with anyio.create_task_group() as tg:
            tg.start_soon(pump_in)
            tg.start_soon(pump_out)
            await lowlevel.run(to_server_r, from_server_w, lowlevel.create_initialization_options())
            tg.cancel_scope.cancel()

    async def scenario() -> Any:
        async with serve(handler, "127.0.0.1", 0, subprotocols=["mcp"]) as server:  # type: ignore[list-item]
            port = next(iter(server.sockets)).getsockname()[1]
            config = McpServerConfig(type="ws", scope="user", url=f"ws://127.0.0.1:{port}")
            conn = await connect_to_server("w", config, cwd=str(tmp_path))
            assert isinstance(conn, ConnectedMCPServer) and conn.cleanup is not None
            try:
                return await call_mcp_tool(conn, "add", {"a": 1, "b": 2}, run_id="r", cwd=str(tmp_path))
            finally:
                await conn.cleanup()

    out = asyncio.run(scenario())
    assert out["content"] == '{"result":3}'
    assert seen["subprotocol"] == "mcp" and seen["agent"] == mtr.get_mcp_user_agent()


def test_a_failing_helper_is_ignored_even_with_output():
    assert mtr.get_mcp_headers_from_helper("srv", helper_config("echo '{\"k\": \"v\"}'; exit 1")) is None


def test_the_helper_gets_an_empty_url_when_there_is_none():
    config = McpServerConfig(type="sse-ide", scope="user", headers_helper=(
        "python3 -c 'import json,os; print(json.dumps({\"U\": os.environ[\"TDDAGENTS_MCP_SERVER_URL\"]}))'"))
    assert mtr.get_mcp_headers_from_helper("srv", config) == {"U": ""}


def test_a_hanging_helper_is_cut_off(monkeypatch):
    import time

    monkeypatch.setattr(mtr, "HEADERS_HELPER_TIMEOUT_SECONDS", 0.2)
    start = time.monotonic()
    assert mtr.get_mcp_headers_from_helper("srv", helper_config("sleep 3")) is None
    assert time.monotonic() - start < 2
