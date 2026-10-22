"""Phase 5 (Part I5): run-scoped agent memory belongs to the run, lives outside the workspace,
and is discarded when the run ends — no shared fallback, nothing left for the next run."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.loop.agents.definition import AgentDefinition
from app.loop.agents.memory import AgentMemoryStore
from app.loop.agents.subagent import create_subagent
from app.loop.context import AppStateStore, tool_context_for


def test_run_scope_without_a_run_id_has_no_shared_fallback(tmp_path):
    store = AgentMemoryStore(base_dir=tmp_path)
    with pytest.raises(ValueError, match="^Run-scoped agent memory needs a run id$"):
        store.get_memory_dir("tester", "run")
    expected = tmp_path / ".tddagents" / "run-memory" / "r1" / "tester"
    assert store.get_memory_dir("tester", "run", run_id="r1") == expected


def _worker_def(memory: str) -> AgentDefinition:
    return AgentDefinition(name="tester", description="d", prompt="p", memory=memory)


def test_a_worker_uses_the_runs_store_and_the_runs_id(tmp_path):
    store = AgentMemoryStore(base_dir=tmp_path / "run-dir")
    parent = tool_context_for(
        AppStateStore(), workspace=SimpleNamespace(root=str(tmp_path / "ws")), run_id="loop-1",
        agent_memory_store=store,
    )
    worker = create_subagent(_worker_def("run"), parent, directive="go")
    assert "run-dir/.tddagents/run-memory/loop-1/tester" in worker.memory_prompt
    assert not (tmp_path / "ws" / ".tddagents").exists()


def test_a_worker_without_a_run_store_cannot_get_run_memory(tmp_path):
    parent = tool_context_for(AppStateStore(), workspace=SimpleNamespace(root=str(tmp_path / "ws")), run_id="loop-1")
    with pytest.raises(ValueError, match="^Run-scoped agent memory needs the run's memory store$"):
        create_subagent(_worker_def("run"), parent, directive="go")
    assert not (tmp_path / "ws" / ".tddagents").exists()
    # An agent with no memory needs no store.
    assert create_subagent(_worker_def("none"), parent, directive="go").memory_prompt == ""


def test_the_runner_discards_run_memory_when_the_run_ends(tmp_path, make_fake_workspace):
    import dataclasses

    from langchain_core.messages import AIMessage

    from app.loop.factory import get_production_deps
    from app.loop.runner import tdd_loop_runner

    seen: list[AgentMemoryStore] = []

    async def model(state, config):
        store = state.tool_context.agent_memory_store
        seen.append(store)
        mdir = store.ensure_memory_dir_exists("tester", "run", run_id=config.run_id)
        (mdir / "MEMORY.md").write_text("scratch")
        yield AIMessage(content="done")

    ws = make_fake_workspace()
    deps = dataclasses.replace(get_production_deps(), call_model=model)
    asyncio.run(tdd_loop_runner("x", 0, {"session_id": "s"}, workspace=ws, deps=deps, project_root=tmp_path,
                                home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    store = seen[0]
    assert isinstance(store, AgentMemoryStore)
    assert not (Path(store.base_dir) / ".tddagents" / "run-memory").exists() or not any(
        (Path(store.base_dir) / ".tddagents" / "run-memory").iterdir()
    )


def test_a_worker_points_at_the_sessions_transcript(tmp_path):
    """Upstream's `getTranscriptPath()` is session-global: a worker compacts against it too."""
    parent = tool_context_for(AppStateStore(), workspace=SimpleNamespace(root=str(tmp_path)), run_id="loop-1",
                              transcript_path="/p/loop-1.jsonl")
    assert create_subagent(_worker_def("none"), parent, directive="go").context.transcript_path == "/p/loop-1.jsonl"
