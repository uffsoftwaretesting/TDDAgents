"""Phase 7: the runner with the Agent tool, and a whole Red -> Green cycle through the
coordinator and real workers on a real workspace."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from langchain_core.messages import AIMessage

from app.coordinator.coordinator_mode import (
    COORDINATOR_MODE_ENV,
    get_coordinator_system_prompt,
)
from app.tdd.event_log import EventLog
from app.utils.session_storage_portable import get_project_dir

CALC_TEST = "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n"
CALC_IMPL = "def add(a, b):\n    return a + b\n"


def _call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def _tool_results(state: Any) -> str:
    from langchain_core.messages import ToolMessage

    return "\n".join(str(m.content) for m in state.messages if isinstance(m, ToolMessage))


def _settings(tmp_path, ws_root) -> Any:
    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / ".tddagents" / "settings.json").write_text(json.dumps({
        "permissions": {"defaultMode": "acceptEdits", "allow": ["Bash(pytest:*)"]}}))
    return project


def _run(workspace, tmp_path, scripts: dict[str | None, list[list[AIMessage]]], seen: list[Any]):
    """A model scripted per agent type (`None` is the main thread)."""
    from app.query.deps import get_production_deps
    from tests.session_run_helpers import run_item

    async def model(state: Any, config: Any):
        agent_type = state.tool_context.agent_type
        seen.append((agent_type, state))
        turns = scripts[agent_type]
        if not turns:
            raise AssertionError(f"{agent_type} asked for more turns than scripted")
        for message in turns.pop(0):
            yield message

    deps = dataclasses.replace(get_production_deps(), call_model=model)
    return asyncio.run(run_item(
        "add two numbers", 2, {"session_id": "s"}, workspace=workspace, deps=deps,
        project_root=_settings(tmp_path, workspace.root), home=tmp_path / "home", managed_dir=tmp_path / "etc"))


def store_ledger_fields(events: Any) -> tuple[bool, bool, bool, float | None]:
    # The done item's `plan_item` event: the plan advance restarts the ledger at once, so the
    # transitions after it no longer show the cycle.
    (done,) = [e for e in events if str(e.event_type) == "plan_item"]
    return (done.tests_written, done.green_passed, done.refactor_passed, done.coverage)


def test_the_coordinator_drives_the_whole_cycle_through_workers(python_workspace, tmp_path, monkeypatch):
    monkeypatch.setenv(COORDINATOR_MODE_ENV, "1")
    scripts: dict[str | None, list[list[AIMessage]]] = {
        None: [
            [_call("Agent", "a1", description="red", subagent_type="tester",
                   prompt="Write tests/test_calc.py testing calculator.add(2, 3) == 5 and run it.")],
            [_call("Agent", "a2", description="green", subagent_type="developer",
                   prompt="Create calculator.py with add(a, b) so tests/test_calc.py passes.")],
            [_call("Agent", "a3", description="refactor", subagent_type="refactorer",
                   prompt="Improve calculator.py and its tests; keep every test green and coverage up.")],
            [AIMessage(content="Red, green and refactor observed.")],
        ],
        "tester": [
            [_call("WriteFile", "t1", file_path="tests/test_calc.py", content=CALC_TEST)],
            [_call("RunTests", "t2", test_path="tests")],
            [AIMessage(content="The test fails: no module named calculator.")],
        ],
        "developer": [
            [_call("WriteFile", "d1", file_path="calculator.py", content=CALC_IMPL)],
            [_call("RunTests", "d2", test_path="tests")],
            [AIMessage(content="All tests pass.")],
        ],
        "refactorer": [
            [_call("RunTests", "r1", test_path="tests")],
            [AIMessage(content="Every test passes with full coverage.")],
        ],
    }
    seen: list[Any] = []
    result = _run(python_workspace, tmp_path, scripts, seen)

    assert (result.status, result.terminal_reason) == ("success", "completed")
    assert (result.tests_written, result.green_passed) == (True, True)
    assert python_workspace.read_file("calculator.py") == CALC_IMPL
    # The coordinator holds only the orchestration tools and the coordinator prompt.
    coordinator = seen[0][1].tool_context
    assert [t.name for t in coordinator.tools] == ["Agent", "TaskStop"]
    assert coordinator.agent_system_prompt == (get_coordinator_system_prompt(),)
    assert "workerToolsContext" in coordinator.extra_user_context
    # The developer only ran once the tester's RunTests had confirmed RED on the shared ledger.
    first_developer = next(state for kind, state in seen if kind == "developer")
    assert first_developer.phase_ledger.tests_written is True
    # The tester's report reached the coordinator as the Agent tool result.
    second_coordinator_turn = [state for kind, state in seen if kind is None][1]
    assert "The test fails: no module named calculator." in _tool_results(second_coordinator_turn)

    project = get_project_dir(str(python_workspace.root))
    (transcript,) = project.glob("loop-*.jsonl")
    sidechains = sorted((project / transcript.stem / "subagents").glob("agent-*.jsonl"))
    assert len(sidechains) == 3
    events = EventLog.load_jsonl(project / transcript.stem / "events.jsonl")
    # Only the coordinator's transitions are the sub-requirement's flow.
    assert [e.transition_reason for e in events.get_transitions()] == [
        "next_turn", "next_turn", "next_turn", "completed"]
    assert store_ledger_fields(events) == (True, True, True, 100.0)
    # The refactorer's batch completed item 0, which the tool boundary records at once: its
    # `plan_item` event, then the transitions name 1 (all done).
    assert [(str(e.event_type), e.payload["plan_index"]) for e in events] == [
        ("transition", 0), ("transition", 0), ("plan_item", 0), ("transition", 1), ("transition", 1)]


def test_a_developer_before_red_is_refused_and_the_coordinator_is_told(python_workspace, tmp_path, monkeypatch):
    monkeypatch.setenv(COORDINATOR_MODE_ENV, "1")
    scripts: dict[str | None, list[list[AIMessage]]] = {
        None: [
            [_call("Agent", "a1", description="green", subagent_type="developer", prompt="Implement add.")],
            [AIMessage(content="stop")],
            [AIMessage(content="stop again")],
            [AIMessage(content="and again")],
        ],
    }
    seen: list[Any] = []
    result = _run(python_workspace, tmp_path, scripts, seen)
    second = [state for kind, state in seen if kind is None][1]
    assert "Agent 'developer' cannot start: Cannot transition to GREEN" in _tool_results(second)
    # Every stop is sent back (Phase 12) until the user aborts; never `completed`.
    assert result.terminal_reason == "aborted_streaming"
    assert not any(kind == "developer" for kind, _ in seen)


def test_the_main_thread_has_the_agent_tool_outside_coordinator_mode(python_workspace, tmp_path, monkeypatch):
    monkeypatch.delenv(COORDINATOR_MODE_ENV, raising=False)
    seen: list[Any] = []
    _run(python_workspace, tmp_path, {None: [[AIMessage(content="d")], [AIMessage(content="d")]]}, seen)
    ctx = seen[0][1].tool_context
    assert "Agent" in [t.name for t in ctx.tools]
    assert ctx.agent_system_prompt is None and ctx.extra_user_context is None
    assert ctx.transcript_store is not None


# ── mutation-gate pins (Phase 7) ────────────────────────────────────────────

def _agent_file(directory, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {name} agent\n---\nYou are {name}.\n")


def _agent_tool(tools: Any) -> Any:
    return next(t for t in tools if t.name == "Agent")


def test_the_agent_tool_sees_project_and_user_agents(tmp_path, monkeypatch):
    from app.cli.print import main_thread_tools
    from app.query.config import build_run_config
    from app.query.deps import get_production_deps

    monkeypatch.delenv(COORDINATOR_MODE_ENV, raising=False)
    _agent_file(tmp_path / "proj" / ".tddagents" / "agents", "projector")
    _agent_file(tmp_path / "home" / ".tddagents" / "agents", "homer")
    config = build_run_config("r")
    tools = main_thread_tools(config, get_production_deps(), tmp_path / "proj", tmp_path / "home")
    prompt = _agent_tool(tools).prompt
    assert "- projector: projector agent" in prompt and "- homer: homer agent" in prompt


def test_the_coordinators_agent_tool_has_the_coordinator_prompt(tmp_path, monkeypatch):
    from app.cli.print import main_thread_tools
    from app.query.config import build_run_config
    from app.query.deps import get_production_deps
    from app.tools.agent_tool.load_agents_dir import (
        get_agent_definitions_with_overrides,
    )
    from app.tools.agent_tool.prompt import get_prompt

    monkeypatch.setenv(COORDINATOR_MODE_ENV, "1")
    config = build_run_config("r")
    tools = main_thread_tools(config, get_production_deps(), tmp_path, tmp_path / "home")
    assert sorted(t.name for t in tools) == ["Agent", "SendMessage", "TaskStop"]
    (tool,) = [t for t in tools if t.name == "Agent"]
    definitions = get_agent_definitions_with_overrides(project_dir=tmp_path, user_home=tmp_path / "home")
    assert tool.prompt == get_prompt(list(definitions.values()), True, default_agent_type=None)
    assert tool.prompt != get_prompt(list(definitions.values()), False, default_agent_type=None)


def test_run_memory_lives_in_the_run_dir_or_a_named_temp_dir(tmp_path):
    from app.cli.print import run_agent_memory_store

    store, root = run_agent_memory_store(tmp_path)
    assert (store.base_dir, root) == (tmp_path.resolve(), None)
    store, root = run_agent_memory_store(None)
    assert root is not None and root.name.startswith("tddagents-agent-memory-")
    assert store.base_dir == root.resolve()
    root.rmdir()


def test_the_runner_wires_its_model_session_and_user_agents(make_fake_workspace, tmp_path, monkeypatch):
    """The cost tracker prices the run's model; events carry the run id; the caller's own
    sink still gets the real events; user agents come from the given home."""
    from app.query.deps import get_production_deps
    from app.query.transitions import Terminated, Transition
    from tests.session_run_helpers import run_item

    monkeypatch.delenv(COORDINATOR_MODE_ENV, raising=False)
    _agent_file(tmp_path / "home" / ".tddagents" / "agents", "homer")
    seen: list[Any] = []
    sunk: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append((state, config))
        yield AIMessage(content="d")

    deps = dataclasses.replace(get_production_deps(), call_model=model, emit_event=sunk.append)
    asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=make_fake_workspace(), deps=deps,
                         project_root=tmp_path, home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    state, config = seen[0]
    assert state.tool_context.cost_tracker.model == config.model
    assert "- homer: homer agent" in _agent_tool(state.tool_context.tools).prompt
    assert sunk and all(isinstance(e, (Transition, Terminated)) for e in sunk)
    project = get_project_dir(str(tmp_path))
    (transcript,) = project.glob("loop-*.jsonl")
    events = EventLog.load_jsonl(project / transcript.stem / "events.jsonl")
    assert {e.session_id for e in events} == {transcript.stem}


def test_agent_memory_lives_in_the_workspaces_run_dir(make_fake_workspace, tmp_path):
    """Part I5: the run's memory store is rooted in the workspace's run dir when it has one."""
    from app.query.deps import get_production_deps
    from tests.session_run_helpers import run_item

    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state.tool_context.agent_memory_store.base_dir)
        yield AIMessage(content="done")

    ws = make_fake_workspace()
    ws.run_dir = str(tmp_path / "run")
    deps = dataclasses.replace(get_production_deps(), call_model=model)
    asyncio.run(run_item("x", 0, {"session_id": "s"}, workspace=ws, deps=deps, project_root=tmp_path,
                         home=tmp_path / "home", managed_dir=tmp_path / "etc"))
    assert str(seen[0]) == str(tmp_path / "run")
