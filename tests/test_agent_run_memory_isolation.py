"""Phase 5 (Part I5): run-scoped agent memory belongs to the run, lives outside the workspace,
and is discarded when the run ends — no shared fallback, nothing left for the next run."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.tools.agent_tool.agent_memory import AgentMemoryStore


def test_run_scope_without_a_run_id_has_no_shared_fallback(tmp_path):
    store = AgentMemoryStore(base_dir=tmp_path)
    with pytest.raises(ValueError, match="^Run-scoped agent memory needs a run id$"):
        store.get_memory_dir("tester", "run")
    expected = tmp_path / ".tddagents" / "run-memory" / "r1" / "tester"
    assert store.get_memory_dir("tester", "run", run_id="r1") == expected


def test_the_runner_discards_run_memory_when_the_run_ends(tmp_path, make_fake_workspace):
    import dataclasses

    from langchain_core.messages import AIMessage

    from app.query.deps import get_production_deps
    from tests.session_run_helpers import run_item

    seen: list[AgentMemoryStore] = []

    async def model(state, config):
        store = state.tool_context.agent_memory_store
        seen.append(store)
        mdir = store.ensure_memory_dir_exists("tester", "run", run_id=config.run_id)
        (mdir / "MEMORY.md").write_text("scratch")
        yield AIMessage(content="done")

    ws = make_fake_workspace()
    deps = dataclasses.replace(get_production_deps(), call_model=model)
    asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=ws, deps=deps, project_root=tmp_path,
                         home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    store = seen[0]
    assert isinstance(store, AgentMemoryStore)
    assert not (Path(store.base_dir) / ".tddagents" / "run-memory").exists() or not any(
        (Path(store.base_dir) / ".tddagents" / "run-memory").iterdir()
    )
