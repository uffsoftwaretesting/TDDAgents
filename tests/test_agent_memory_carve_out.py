"""
Agent memory through the ordinary file tools (2026-10-28), as upstream's `isAgentMemoryPath`
branches of `checkEditableInternalPath` / `checkReadableInternalPath` allow it.

A live run's tester could neither read nor write its run-scoped MEMORY.md: the store lives
outside the project on purpose (I5), the file tools are bounded to the project, and the gate
asked. It wrote the notes into the project root instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import tool_context_for
from app.tools.agent_tool.agent_memory import AgentMemoryStore, agent_memory_target
from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.workspace.local import LocalWorkspace


def store_in(tmp_path: Path) -> AgentMemoryStore:
    return AgentMemoryStore(base_dir=tmp_path / "thread", user_home=tmp_path / "home")


def test_memory_roots_are_this_runs_and_the_shared_scopes(tmp_path: Path):
    store = store_in(tmp_path)
    base = store.base_dir / ".tddagents"
    assert store.memory_roots("r1") == tuple(str(p) for p in (
        base / "run-memory" / "r1", base / "session-memory", base / "agent-memory-local", base / "agent-memory",
        store.user_home / ".tddagents" / "agent-memory"))
    assert str(base / "run-memory" / "r1") not in store.memory_roots(None)


@pytest.mark.parametrize("relative, is_memory", [
    (".tddagents/run-memory/r1/tester/MEMORY.md", True),
    (".tddagents/run-memory/r2/tester/MEMORY.md", False),  # another run's: isolation (I5)
    (".tddagents/run-memory/r1", False),  # the root itself is no file in it
    (".tddagents/run-memory/r1/../r2/tester/MEMORY.md", False),
    (".tddagents/agent-memory/tester/MEMORY.md", True),
    (".tddagents/session-memory/s/tester/MEMORY.md", True),
    (".tddagents/settings.json", False),
    ("project/notes.md", False),
])
def test_is_memory_path(tmp_path: Path, relative: str, is_memory: bool):
    store = store_in(tmp_path)
    assert store.is_memory_path(f"{store.base_dir}/{relative}", "r1") is is_memory


def test_the_target_needs_an_absolute_path_and_a_store(tmp_path: Path):
    store = store_in(tmp_path)
    memory = f"{store.base_dir}/.tddagents/run-memory/r1/tester/MEMORY.md"

    class Ctx:
        agent_memory_store = store
        run_id = "r1"

    assert agent_memory_target(Ctx(), memory) == memory
    assert agent_memory_target(Ctx(), f"{store.base_dir}/.tddagents/run-memory/r1/x/../tester/M.md") == (
        f"{store.base_dir}/.tddagents/run-memory/r1/tester/M.md")
    assert agent_memory_target(Ctx(), ".tddagents/run-memory/r1/tester/MEMORY.md") is None
    assert agent_memory_target(Ctx(), "") is None
    assert agent_memory_target(object(), memory) is None


def worker(tmp_path: Path, phase: TddPhase = TddPhase.RED) -> Any:
    """A headless worker (no one to ask) in `default` mode, as the live tester was."""
    store = store_in(tmp_path)
    ws = LocalWorkspace(str(tmp_path / "thread" / "project"))
    perm = ToolPermissionContext(mode=PermissionMode.DEFAULT, should_avoid_permission_prompts=True)
    app = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=phase), tool_permission_context=perm))
    return tool_context_for(app, workspace=ws, run_id="r1", agent_memory_store=store), store


def call(builder: Any, args: dict[str, Any], ctx: Any) -> Any:
    tool = builder()
    ctx.tools = (tool,)
    tool_call = ToolCall(id="c1", name=tool.name, args=args)
    return asyncio.run(run_tool_use(tool_call, AIMessage(content="", tool_calls=[tool_call]), ctx, [tool])).message


@pytest.mark.parametrize("phase", [TddPhase.RED, TddPhase.GREEN, TddPhase.REFACTOR])
def test_a_worker_reads_writes_and_edits_its_memory_in_every_phase(tmp_path: Path, phase: TddPhase):
    ctx, store = worker(tmp_path, phase)
    memory = str(store.base_dir / ".tddagents" / "run-memory" / "r1" / "tester" / "MEMORY.md")
    written = call(build_write_file_tool, {"file_path": memory, "content": "- ÄbA needs ÄbÄ\n"}, ctx)
    assert written.status == "success", written.content
    assert Path(memory).read_text(encoding="utf-8") == "- ÄbA needs ÄbÄ\n"
    read = call(build_read_file_tool, {"file_path": memory}, ctx)
    assert read.status == "success" and "ÄbA needs ÄbÄ" in read.content
    edited = call(build_edit_tool, {"file_path": memory, "old_string": "needs", "new_string": "reads as"}, ctx)
    assert edited.status == "success", edited.content
    assert Path(memory).read_text(encoding="utf-8") == "- ÄbA reads as ÄbÄ\n"
    # Nothing landed in the project.
    assert not any(Path(ctx.workspace.root).rglob("MEMORY.md"))


def test_another_runs_memory_is_not_reachable(tmp_path: Path):
    ctx, store = worker(tmp_path)
    other = store.base_dir / ".tddagents" / "run-memory" / "r2" / "tester" / "MEMORY.md"
    other.parent.mkdir(parents=True)
    other.write_text("secret", encoding="utf-8")
    read = call(build_read_file_tool, {"file_path": str(other)}, ctx)
    assert read.status == "error" and "secret" not in read.content
    written = call(build_write_file_tool, {"file_path": str(other), "content": "x"}, ctx)
    assert written.status == "error"
    assert other.read_text(encoding="utf-8") == "secret"


def test_red_still_refuses_production_code(tmp_path: Path):
    ctx, _ = worker(tmp_path)
    refused = call(build_write_file_tool, {"file_path": "calc.py", "content": "x = 1\n"}, ctx)
    assert refused.status == "error" and "TDD phase RED denies writing to production file" in refused.content
