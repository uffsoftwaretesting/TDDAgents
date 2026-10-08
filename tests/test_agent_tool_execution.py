"""
Unit tests for I3: Agent tool execution, worker pool assembly, and phase invariants.
"""

from pathlib import Path
from typing import Sequence
import pytest

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.fork import build_child_message
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.tool import (
    AGENT_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
    build_agent_tool,
)
from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext, discard_app_state_update
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.messages import Message
from app.loop.tools.base import Tool, build_tool
from app.loop.tools.types import ToolResult


def _make_dummy_tool(name: str) -> Tool:
    return build_tool(
        name=name,
        prompt=f"Dummy {name}",
        call=lambda args, ctx: ToolResult(content=f"executed {name}"),
    )


def _make_context(
    messages: Sequence[Message] = (),
    tools: Sequence[Tool] = (),
    phase: TddPhase = TddPhase.RED,
) -> ToolContext:
    ledger = PhaseLedger(phase=phase)
    store = AppStateStore(AppState(phase_ledger=ledger))
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=tuple(messages),
        tools=tuple(tools),
    )


@pytest.mark.anyio
async def test_agent_tool_schema_and_validation() -> None:
    tool = build_agent_tool()
    assert tool.name == AGENT_TOOL_NAME
    assert LEGACY_AGENT_TOOL_NAME in tool.aliases

    ctx = _make_context()
    # Missing prompt
    val_fail = tool.validate_input({}, ctx)
    assert val_fail.valid is False
    assert "Missing required parameter 'prompt'" in str(val_fail.message)

    # Valid prompt
    val_ok = tool.validate_input({"prompt": "Do work"}, ctx)
    assert val_ok.valid is True


@pytest.mark.anyio
async def test_agent_tool_sync_delegation_success() -> None:
    t_read = _make_dummy_tool("ReadFile")
    t_write = _make_dummy_tool("WriteFile")
    t_agent = _make_dummy_tool("Agent")

    dev_def = AgentDefinition(
        name="developer",
        description="developer agent",
        prompt="Write code",
        phase=TddPhase.GREEN,
        tools=("ReadFile", "WriteFile"),
    )
    definitions = {"developer": dev_def}

    captured_worker_context = []

    async def mock_runner(
        agent_def: AgentDefinition,
        directive: str,
        child_messages: Sequence[Message],
        worker_tools: Sequence[Tool],
        worker_context: ToolContext,
        memory_prompt: str,
    ) -> tuple[str, str | None]:
        captured_worker_context.append(worker_context)
        # Verify worker context has discard_app_state_update as setter
        assert worker_context.set_app_state is discard_app_state_update
        # Verify worker pool does not have Agent tool (prevent recursion)
        worker_tool_names = [t.name for t in worker_tools]
        assert "Agent" not in worker_tool_names
        assert "ReadFile" in worker_tool_names
        return f"Work complete for {directive}", "completed"

    tool = build_agent_tool(
        available_tools=[t_read, t_write, t_agent],
        definitions=definitions,
        subagent_runner=mock_runner,
    )

    ctx = _make_context(phase=TddPhase.GREEN)
    res = await tool.call({"subagent_type": "developer", "prompt": "Implement auth"}, ctx)

    assert res.is_error is False
    assert "Work complete for Implement auth" in res.content
    assert res.metadata is not None
    assert res.metadata["status"] == "completed"
    assert res.metadata["agent_type"] == "developer"


@pytest.mark.anyio
async def test_worker_gets_a_copy_of_read_file_state() -> None:
    """createSubagentContext clones readFileState: the worker sees the parent's reads, and
    its own reads never leak back."""
    from app.loop.context import FileState

    dev_def = AgentDefinition(
        name="developer", description="d", prompt="p", phase=TddPhase.GREEN, tools=("ReadFile",),
    )
    seen = {}

    async def runner(agent_def, directive, child_messages, worker_tools, worker_context, memory_prompt):
        seen["state"] = dict(worker_context.read_file_state)
        worker_context.read_file_state["worker.txt"] = FileState(content="w")
        return "ok", "completed"

    tool = build_agent_tool(
        available_tools=[_make_dummy_tool("ReadFile")], definitions={"developer": dev_def}, subagent_runner=runner,
    )
    ctx = _make_context(phase=TddPhase.GREEN)
    ctx.read_file_state["parent.txt"] = FileState(content="p")
    await tool.call({"subagent_type": "developer", "prompt": "x"}, ctx)

    assert seen["state"] == {"parent.txt": FileState(content="p")}
    assert ctx.read_file_state == {"parent.txt": FileState(content="p")}


@pytest.mark.anyio
async def test_agent_tool_async_delegation() -> None:
    tool = build_agent_tool(
        definitions={
            "explore": AgentDefinition(name="explore", description="explore", prompt="explore")
        }
    )
    ctx = _make_context()
    res = await tool.call(
        {"subagent_type": "explore", "prompt": "Scan dir", "run_in_background": True},
        ctx,
    )
    assert res.is_error is False
    assert "launched in background" in res.content
    assert res.metadata is not None
    assert res.metadata["status"] == "async_launched"
    assert res.metadata["agent_type"] == "explore"


@pytest.mark.anyio
async def test_agent_tool_background_frontmatter() -> None:
    agent_bg = AgentDefinition(
        name="bg_worker",
        description="bg",
        prompt="bg",
        background=True,
    )
    tool = build_agent_tool(definitions={"bg_worker": agent_bg})
    ctx = _make_context()
    res = await tool.call({"subagent_type": "bg_worker", "prompt": "Do bg work"}, ctx)
    assert res.metadata is not None
    assert res.metadata["status"] == "async_launched"


@pytest.mark.anyio
async def test_agent_tool_unknown_agent_error() -> None:
    tool = build_agent_tool(definitions={})
    ctx = _make_context()
    res = await tool.call({"subagent_type": "ghost", "prompt": "Boo"}, ctx)
    assert res.is_error is True
    assert "Unknown agent type 'ghost'" in res.content


@pytest.mark.anyio
async def test_agent_tool_tdd_phase_invariant_enforcement() -> None:
    dev_def = AgentDefinition(
        name="developer",
        description="dev",
        prompt="dev",
        phase=TddPhase.GREEN,
    )
    tester_def = AgentDefinition(
        name="tester",
        description="test",
        prompt="test",
        phase=TddPhase.RED,
    )
    defs = {"developer": dev_def, "tester": tester_def}
    tool = build_agent_tool(definitions=defs)

    # Current ledger in RED
    ctx_red = _make_context(phase=TddPhase.RED)

    # Attempting to run developer in RED must be denied
    res_denied = await tool.call({"subagent_type": "developer", "prompt": "Code now"}, ctx_red)
    assert res_denied.is_error is True
    assert "Denied: Agent 'developer' requires phase GREEN" in res_denied.content

    # Running tester in RED is allowed
    res_allowed = await tool.call({"subagent_type": "tester", "prompt": "Write test"}, ctx_red)
    assert res_allowed.is_error is False
    assert "Subagent 'tester' completed task successfully" in res_allowed.content


@pytest.mark.anyio
async def test_agent_tool_fork_path_and_guard() -> None:
    tool = build_agent_tool(definitions={})

    parent_history = [
        HumanMessage(content="User task"),
        AIMessage(
            content="Plan",
            tool_calls=[{"name": "ReadFile", "args": {"path": "main.py"}, "id": "tc1"}],
        ),
    ]
    ctx = _make_context(messages=parent_history)

    # Omit subagent_type -> fork path
    res = await tool.call({"prompt": "Inspect auth in fork"}, ctx)
    assert res.is_error is False
    assert "Subagent 'fork' completed task successfully" in res.content

    # Now if messages already contain fork boilerplate, recursive fork is denied
    in_fork_ctx = _make_context(
        messages=[HumanMessage(content=build_child_message("Already in fork"))]
    )
    res_recur = await tool.call({"prompt": "Nested fork"}, in_fork_ctx)
    assert res_recur.is_error is True
    assert "Fork children cannot recursively spawn fork subagents" in res_recur.content


@pytest.mark.anyio
async def test_agent_tool_run_memory_purged_on_completion(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path)
    mem_agent = AgentDefinition(
        name="researcher",
        description="researcher",
        prompt="research",
        memory="run",
    )
    captured_run_id = []

    async def mock_runner(
        agent_def: AgentDefinition,
        directive: str,
        child_messages: Sequence[Message],
        worker_tools: Sequence[Tool],
        worker_context: ToolContext,
        memory_prompt: str,
    ) -> tuple[str, str | None]:
        # Memory prompt should have been injected
        assert "# Agent Persistent Memory" in memory_prompt
        # Store some scratch file in the run memory dir
        # Extract run_id from memory_prompt path
        for part in memory_prompt.split("/"):
            if part.startswith("run_"):
                if part not in captured_run_id:
                    captured_run_id.append(part)
                    mem_dir = store.get_memory_dir("researcher", "run", run_id=part)
                    assert mem_dir is not None
                    (mem_dir / "notes.txt").write_text("Interim finding")
        return "Research finished", "completed"

    tool = build_agent_tool(
        definitions={"researcher": mem_agent},
        memory_store=store,
        subagent_runner=mock_runner,
    )

    ctx = _make_context()
    res = await tool.call({"subagent_type": "researcher", "prompt": "Dig deep"}, ctx)

    assert res.is_error is False
    assert len(captured_run_id) == 1
    rid = captured_run_id[0]
    assert res.metadata is not None
    assert res.metadata["run_id"] == rid
    # Verify that run memory dir was purged after call
    run_dir = tmp_path / ".tddagents" / "run-memory" / rid
    assert not run_dir.exists()
