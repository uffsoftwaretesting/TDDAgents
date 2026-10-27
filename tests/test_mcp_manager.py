"""`app/services/mcp/mcp_connection_manager.py`: connecting a run's servers, collecting their tools, shutting down."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.client._memory import InMemoryTransport

import app.services.mcp.mcp_connection_manager as mm
from app.services.mcp.config import get_global_config_path
from app.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpServerConfig,
)
from tests.test_mcp_client import demo_server

LOCAL = McpServerConfig(type="stdio", scope="project", command="c")
REMOTE = McpServerConfig(type="http", scope="user", url="http://x")


def test_local_and_included():
    assert mm.is_local_mcp_server(LOCAL)
    assert mm.is_local_mcp_server(McpServerConfig(type=None, scope="user", command="c"))
    assert mm.is_local_mcp_server(McpServerConfig(type="sdk", scope="user"))
    assert not mm.is_local_mcp_server(REMOTE)
    assert mm.is_included_mcp_tool("mcp__x__y")
    assert mm.is_included_mcp_tool("mcp__ide__getDiagnostics") and mm.is_included_mcp_tool("mcp__ide__executeCode")
    assert not mm.is_included_mcp_tool("mcp__ide__other")


def test_batch_sizes(monkeypatch):
    monkeypatch.delenv(mm.LOCAL_BATCH_SIZE_ENV, raising=False)
    monkeypatch.delenv(mm.REMOTE_BATCH_SIZE_ENV, raising=False)
    assert mm.get_mcp_server_connection_batch_size() == 3
    assert mm.get_remote_mcp_server_connection_batch_size() == 20
    monkeypatch.setenv(mm.LOCAL_BATCH_SIZE_ENV, "5")
    monkeypatch.setenv(mm.REMOTE_BATCH_SIZE_ENV, "7")
    assert mm.get_mcp_server_connection_batch_size() == 5
    assert mm.get_remote_mcp_server_connection_batch_size() == 7


@pytest.mark.parametrize(("concurrency", "peak"), [(2, 2), (0, 1), (10, 5)])
def test_process_batched_bounds_concurrency(concurrency, peak):
    running, seen = [0], [0]

    async def work(_item: Any) -> None:
        running[0] += 1
        seen[0] = max(seen[0], running[0])
        await asyncio.sleep(0.01)
        running[0] -= 1

    asyncio.run(mm.process_batched(range(5), concurrency, work))
    assert seen[0] == peak


def manager(tmp_path: Any, **servers: Any) -> mm.McpConnectionManager:
    def factory(name: str, _config: McpServerConfig) -> Any:
        target = servers[name]
        if isinstance(target, Exception):
            raise target
        return InMemoryTransport(target)

    return mm.McpConnectionManager(cwd=str(tmp_path), transport_factory=factory)


def test_connect_collects_clients_tools_and_resource_tools_once(tmp_path):
    mgr = manager(tmp_path, a=demo_server(), b=demo_server(), bad=OSError("no"))

    async def scenario() -> None:
        await mgr.connect({"a": LOCAL, "b": REMOTE, "bad": LOCAL})
        try:
            by_name = {c.name: c for c in mgr.clients}
            assert isinstance(by_name["a"], ConnectedMCPServer) and isinstance(by_name["b"], ConnectedMCPServer)
            assert isinstance(by_name["bad"], FailedMCPServer) and by_name["bad"].error == "no"
            names = [t.name for t in mgr.tools]
            assert sorted(n for n in names if n.startswith("mcp__a__")) == [
                "mcp__a__add", "mcp__a__big", "mcp__a__boom", "mcp__a__embedded", "mcp__a__picture"]
            assert len([n for n in names if n.startswith("mcp__b__")]) == 5
            assert names.count("ListMcpResourcesTool") == 1 and names.count("ReadMcpResourceTool") == 1
        finally:
            await mgr.aclose()

    asyncio.run(scenario())


def test_a_disabled_server_is_recorded_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    from app.services.mcp.config import get_project_config_key
    get_global_config_path().write_text(json.dumps(
        {"projects": {get_project_config_key(tmp_path): {"disabledMcpServers": ["off"]}}}))
    mgr = manager(tmp_path, off=OSError("must not connect"))
    asyncio.run(mgr.connect({"off": LOCAL}))
    assert [type(c) for c in mgr.clients] == [DisabledMCPServer] and mgr.tools == []


def test_a_server_whose_tool_listing_explodes_is_failed(tmp_path, monkeypatch):
    async def explode(_client: Any) -> Any:
        raise RuntimeError("listing broke")

    monkeypatch.setattr(mm, "fetch_tools_for_client", explode)
    mgr = manager(tmp_path, a=demo_server())

    async def scenario() -> None:
        await mgr.connect({"a": LOCAL})
        await mgr.aclose()

    asyncio.run(scenario())
    assert isinstance(mgr.clients[-1], FailedMCPServer) and mgr.clients[-1].error == "listing broke"


def test_no_resource_tools_without_a_resource_server(tmp_path, monkeypatch):
    from types import SimpleNamespace

    async def connect(name: str, config: Any, **_: Any) -> Any:
        return ConnectedMCPServer(name=name, config=config, session=None,
                                  capabilities=SimpleNamespace(tools=object(), resources=None))

    async def tools(_client: Any) -> Any:
        return [SimpleNamespace(name="ping", description="", annotations=None, input_schema={})]

    monkeypatch.setattr(mm, "connect_to_server", connect)
    monkeypatch.setattr(mm, "fetch_tools_for_client", tools)
    mgr = mm.McpConnectionManager(cwd=str(tmp_path))
    asyncio.run(mgr.connect({"p": LOCAL}))
    assert [t.name for t in mgr.tools] == ["mcp__p__ping"]


def test_aclose_closes_every_session(tmp_path):
    closed: list[str] = []

    async def cleanup_a() -> None:
        closed.append("a")

    async def cleanup_b() -> None:
        raise RuntimeError("ignored")

    mgr = mm.McpConnectionManager(cwd=str(tmp_path))
    mgr.clients = [
        ConnectedMCPServer(name="a", config=LOCAL, session=None, capabilities=None, cleanup=cleanup_a),
        ConnectedMCPServer(name="b", config=LOCAL, session=None, capabilities=None, cleanup=cleanup_b),
        ConnectedMCPServer(name="c", config=LOCAL, session=None, capabilities=None),
        FailedMCPServer(name="d", config=LOCAL),
    ]
    asyncio.run(mgr.aclose())
    assert closed == ["a"]


def test_the_clients_feed_the_instructions_delta(tmp_path, monkeypatch):
    """`utils/attachments.py` reads `type`, `name` and `instructions` off the manager's clients."""
    from types import SimpleNamespace

    from app.utils.attachments import (
        MCP_INSTR_DELTA_ENV,
        get_mcp_instructions_delta_attachment,
    )

    monkeypatch.setenv(MCP_INSTR_DELTA_ENV, "1")
    mgr = manager(tmp_path, a=demo_server(), bad=OSError("no"))

    async def scenario() -> Any:
        await mgr.connect({"a": LOCAL, "bad": LOCAL})
        try:
            return get_mcp_instructions_delta_attachment((), SimpleNamespace(mcp_clients=tuple(mgr.clients)))
        finally:
            await mgr.aclose()

    [attachment] = asyncio.run(scenario())
    assert attachment.type == "mcp_instructions_delta"
    assert dict(attachment.data) == {"addedNames": ["a"], "addedBlocks": ["## a\nUse add."], "removedNames": []}
