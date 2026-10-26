"""Phase 8: the runner waits for background work and feeds its notifications back as turns
(`cli/print.ts`, the `do … while (waitingForAgents)` loop)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.command_queue import QueuedCommand
from app.loop.deps import StopHookResult
from app.loop.tasks.task import create_task_state_base


def _settings(tmp_path) -> Any:
    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / ".tddagents" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "acceptEdits"}}))
    return project


async def _no_stop_hooks(state: Any, config: Any) -> StopHookResult:
    return StopHookResult()


def _run(workspace, tmp_path, model, *, stop_hooks: Any = _no_stop_hooks):
    from app.loop.factory import get_production_deps
    from app.loop.runner import tdd_loop_runner

    deps = dataclasses.replace(get_production_deps(), call_model=model, stop_hooks=stop_hooks)
    return asyncio.run(tdd_loop_runner(
        "add two numbers", 0, {"session_id": "s"}, workspace=workspace, deps=deps,
        project_root=_settings(tmp_path), home=tmp_path / "home", managed_dir=tmp_path / "etc"))


#: Strong references to the fake background tasks (the event loop holds only weak ones).
_BACKGROUND: list[Any] = []


def _set_task_status(ctx: Any, task_id: str, status: str) -> None:
    ctx.set_app_state(lambda s: dataclasses.replace(
        s, tasks={**s.tasks, task_id: dataclasses.replace(s.tasks[task_id], status=status)}))


def _start_background_task(ctx: Any, *, delay: float, note: str, agent_id: str | None = None) -> None:
    """A running background task that, after `delay`, completes and enqueues its notification."""
    task = dataclasses.replace(create_task_state_base("a1", "local_agent", "explore", "/tmp/a1.output"),
                               status="running")
    ctx.set_app_state(lambda s: dataclasses.replace(s, tasks={**s.tasks, "a1": task}))

    async def finish() -> None:
        await asyncio.sleep(delay)
        _set_task_status(ctx, "a1", "completed")
        ctx.command_queue.enqueue_pending_notification(
            QueuedCommand(value=note, mode="task-notification", agent_id=agent_id))

    _BACKGROUND.append(asyncio.ensure_future(finish()))


def test_a_notification_from_a_background_task_becomes_a_turn_of_the_same_conversation(python_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            _start_background_task(state.tool_context, delay=0.2, note="<task-notification>done</task-notification>")
            yield AIMessage(content="Launched explore in the background.")
        else:
            yield AIMessage(content="Explore reported back.")

    result = _run(python_workspace, tmp_path, model)

    assert len(seen) == 2
    follow_up = seen[1].messages
    # The same conversation: the first turn's prompt and answer precede the notification.
    texts = [str(m.content) for m in follow_up]
    assert "add two numbers" in texts
    assert "Launched explore in the background." in texts
    notification = next(m for m in follow_up if m.content == "<task-notification>done</task-notification>")
    assert isinstance(notification, HumanMessage)
    assert follow_up.index(notification) > texts.index("Launched explore in the background.")
    assert result.terminal_reason == "completed"


def test_the_run_ends_when_no_background_task_runs_and_nothing_is_queued(python_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    assert len(seen) == 1


def test_a_command_addressed_to_a_worker_is_not_a_main_thread_turn(python_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            _start_background_task(state.tool_context, delay=0.05, note="for the worker", agent_id="a1")
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    assert len(seen) == 1
    (left,) = seen[0].tool_context.command_queue.snapshot()
    assert left.agent_id == "a1"


def test_a_queued_main_thread_notification_is_delivered_even_with_no_task_running(python_workspace, tmp_path):
    """`hasMainThreadQueued`: an asyncRewake note alone keeps the run going for one more turn."""
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            state.tool_context.command_queue.enqueue_pending_notification(
                QueuedCommand(value="rewake note", mode="task-notification"))
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    assert len(seen) == 2
    assert any(m.content == "rewake note" for m in seen[1].messages)


def test_the_outcome_is_the_last_turns_terminal(python_workspace, tmp_path):
    """The stop hook guards every turn; the follow-up's terminal is the sub-requirement's."""
    seen: list[Any] = []
    calls: list[int] = []

    async def stop_hooks(state: Any, config: Any) -> StopHookResult:
        calls.append(len(seen))
        return StopHookResult(prevent_continuation=len(seen) > 1)

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            state.tool_context.command_queue.enqueue_pending_notification(
                QueuedCommand(value="note", mode="task-notification"))
        yield AIMessage(content="done")

    result = _run(python_workspace, tmp_path, model, stop_hooks=stop_hooks)
    assert calls == [1, 2]
    assert (result.status, result.terminal_reason) == ("failed", "stop_hook_prevented")


def test_the_context_queue_is_the_hook_registrys(python_workspace, tmp_path):
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    ctx = seen[0].tool_context
    assert ctx.command_queue is ctx.hook_dispatcher.registry.queue


def test_an_unsupported_queued_mode_is_an_error(python_workspace, tmp_path):
    """`print.ts`: only prompt, orphaned-permission and task-notification run in headless mode."""
    from app.loop.command_queue import CommandQueue
    from app.loop.runner import next_main_thread_command

    queue = CommandQueue()
    queue.enqueue(QueuedCommand(value="/x", mode="bash"))
    with pytest.raises(ValueError, match="only prompt commands are supported"):
        next_main_thread_command(queue)


def _tool_names(workspace, tmp_path) -> list[str]:
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run(workspace, tmp_path, model)
    return [t.name for t in seen[0].tool_context.tools]


def test_the_main_thread_holds_the_task_tools(python_workspace, tmp_path, monkeypatch):
    """`getAllBaseTools`: TaskOutput and TaskStop always; SendMessage only with agent teams on."""
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    monkeypatch.delenv("TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS", raising=False)
    names = _tool_names(python_workspace, tmp_path)
    assert {"Agent", "TaskOutput", "TaskStop"} <= set(names)
    assert "SendMessage" not in names


def test_send_message_joins_with_agent_teams(python_workspace, tmp_path, monkeypatch):
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    monkeypatch.setenv("TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS", "1")
    assert "SendMessage" in _tool_names(python_workspace, tmp_path)


def test_the_coordinator_keeps_upstreams_orchestration_tools(python_workspace, tmp_path, monkeypatch):
    """`COORDINATOR_MODE_ALLOWED_TOOLS`: Agent, TaskStop, SendMessage."""
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "1")
    monkeypatch.setenv("TDDAGENTS_EXPERIMENTAL_AGENT_TEAMS", "1")
    assert sorted(_tool_names(python_workspace, tmp_path)) == ["Agent", "SendMessage", "TaskStop"]


def test_the_run_loads_one_skill_registry_and_offers_the_skill_tool(python_workspace, tmp_path, monkeypatch):
    """`getSkills` once per session; the Skill tool sits in the main pool and the workers'."""
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    ctx = seen[0].tool_context
    names = {c.name for c in ctx.skill_registry.get_skill_tool_commands()}
    assert {"tdd-test-design", "tdd-refactor-clean", "tdd-mutation-defense"} <= names
    assert "Skill" in [t.name for t in ctx.tools]


def test_workers_draw_the_skill_tool_too(tmp_path, monkeypatch):
    import app.loop.agents.tool as agent_tool_mod
    from app.loop.config import build_run_config
    from app.loop.factory import get_production_deps
    from app.loop.runner import main_thread_tools
    from app.loop.skills.loader import get_skill_registry

    captured: dict[str, Any] = {}
    real = agent_tool_mod.build_agent_tool

    def spy(**kw: Any) -> Any:
        captured["worker_tools"] = kw["worker_tools"]
        return real(**kw)

    monkeypatch.setattr(agent_tool_mod, "build_agent_tool", spy)
    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    config = build_run_config("r", postgres_checkpointing=False)
    registry = get_skill_registry(tmp_path, tmp_path / "home", tmp_path / "etc")
    main_thread_tools(config, get_production_deps(), tmp_path, tmp_path / "home", skill_registry=registry)
    assert "Skill" in [t.name for t in captured["worker_tools"]]


# ── MCP ─────────────────────────────────────────────────────────────────────

def _mcp_project(tmp_path, allow: list[str]) -> Any:
    import sys
    from pathlib import Path

    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / ".tddagents" / "settings.json").write_text(json.dumps(
        {"permissions": {"defaultMode": "acceptEdits", "allow": allow}}))
    server = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo": {
        "command": sys.executable, "args": [str(server)], "env": {"DEMO_INSTRUCTIONS": "Echo politely."}}}}))
    return project


def _run_mcp(workspace, tmp_path, model, allow: list[str]):
    from app.loop.factory import get_production_deps
    from app.loop.runner import tdd_loop_runner

    deps = dataclasses.replace(get_production_deps(), call_model=model, stop_hooks=_no_stop_hooks)
    return asyncio.run(tdd_loop_runner(
        "add two numbers", 0, {"session_id": "s"}, workspace=workspace, deps=deps,
        project_root=_mcp_project(tmp_path, allow), home=tmp_path / "home", managed_dir=tmp_path / "etc"))


def test_the_projects_mcp_servers_are_connected_offered_and_closed(python_workspace, tmp_path, monkeypatch):
    from langchain_core.messages import ToolMessage

    from app.loop.mcp.manager import McpConnectionManager

    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    closed: list[int] = []
    real_close = McpConnectionManager.aclose

    async def spy_close(self: Any) -> None:
        closed.append(len(self.clients))
        await real_close(self)

    monkeypatch.setattr(McpConnectionManager, "aclose", spy_close)
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            yield AIMessage(content="", tool_calls=[{"name": "mcp__demo__echo", "args": {"text": "hi"}, "id": "m1"}])
        else:
            yield AIMessage(content="done")

    _run_mcp(python_workspace, tmp_path, model, ["mcp__demo"])
    ctx = seen[0].tool_context
    ((server,),) = [[c for c in ctx.mcp_clients if c.name == "demo"]]
    assert (server.type, server.instructions) == ("connected", "Echo politely.")
    assert "mcp__demo__echo" in [t.name for t in ctx.tools]
    (result,) = [m for m in seen[1].messages if isinstance(m, ToolMessage)]
    assert "hi" in str(result.content) and result.status != "error"
    assert closed == [1]


def test_an_mcp_call_without_an_allow_rule_is_denied_headless(python_workspace, tmp_path, monkeypatch):
    """`MCPTool.checkPermissions` is passthrough: with no rule the headless ask is a deny."""
    from langchain_core.messages import ToolMessage

    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            yield AIMessage(content="", tool_calls=[{"name": "mcp__demo__echo", "args": {"text": "hi"}, "id": "m1"}])
        else:
            yield AIMessage(content="done")

    _run_mcp(python_workspace, tmp_path, model, [])
    (result,) = [m for m in seen[1].messages if isinstance(m, ToolMessage)]
    assert result.status == "error"
    assert "No such tool" not in str(result.content) and "hi" != str(result.content)
    assert "mcp__demo__echo" in [t.name for t in seen[0].tool_context.tools]


def test_the_coordinator_hands_mcp_tools_to_its_workers_only(python_workspace, tmp_path, monkeypatch):
    """`mergeAndFilterTools`: the coordinator filter runs over the merged pool, MCP included."""
    import app.loop.agents.tool as agent_tool_mod

    captured: dict[str, Any] = {}
    real = agent_tool_mod.build_agent_tool

    def spy(**kw: Any) -> Any:
        captured["worker_tools"] = kw["worker_tools"]
        return real(**kw)

    monkeypatch.setattr(agent_tool_mod, "build_agent_tool", spy)
    monkeypatch.setenv("TDDAGENTS_COORDINATOR_MODE", "1")
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run_mcp(python_workspace, tmp_path, model, ["mcp__demo"])
    assert not any(t.name.startswith("mcp__") for t in seen[0].tool_context.tools)
    assert "mcp__demo__echo" in [t.name for t in captured["worker_tools"]]


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_a_follow_up_turn_carries_its_attachments(python_workspace, tmp_path):
    """The notification turn is user input: the attachments it brings (a moved ledger) follow it."""
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        if len(seen) == 1:
            ctx = state.tool_context
            ctx.set_app_state(lambda s: dataclasses.replace(s, phase_ledger=s.phase_ledger.with_test_result(1)))
            ctx.command_queue.enqueue_pending_notification(QueuedCommand(value="note", mode="task-notification"))
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    follow_up = seen[1].messages
    note = next(i for i, m in enumerate(follow_up) if m.content == "note")
    kinds = [(m.additional_kwargs.get("attachment") or {}).get("type") for m in follow_up[note + 1:]]
    assert "tdd_state" in kinds
    assert seen[1].tool_context.messages == follow_up


def test_the_runner_hands_its_scopes_to_skills_mcp_and_the_task_tools(python_workspace, tmp_path, monkeypatch):
    import app.loop.runner as runner_mod
    import app.loop.skills.loader as skill_loader
    import app.loop.skills.tool as skill_tool_mod
    import app.loop.tools.send_message as send_mod

    captured: dict[str, Any] = {}
    real_registry, real_skill, real_send = (
        skill_loader.get_skill_registry, skill_tool_mod.build_skill_tool, send_mod.build_send_message_tool)
    real_connect = runner_mod.connect_mcp_servers

    def registry(*args: Any, **kw: Any) -> Any:
        captured["registry_args"] = (args, kw)
        captured["registry"] = real_registry(*args, **kw)
        return captured["registry"]

    def skill(reg: Any, **kw: Any) -> Any:
        captured["skill"] = (reg, kw)
        return real_skill(reg, **kw)

    def send(**kw: Any) -> Any:
        captured["send"] = kw
        return real_send(**kw)

    async def connect(root: Any, locations: Any) -> Any:
        captured["locations"] = locations
        return await real_connect(root, locations)

    monkeypatch.setattr(skill_loader, "get_skill_registry", registry)
    monkeypatch.setattr(skill_tool_mod, "build_skill_tool", skill)
    monkeypatch.setattr(send_mod, "build_send_message_tool", send)
    monkeypatch.setattr(runner_mod, "connect_mcp_servers", connect)
    seen: list[Any] = []

    async def model(state: Any, config: Any):
        seen.append(state)
        yield AIMessage(content="done")

    _run(python_workspace, tmp_path, model)
    project = tmp_path / "project"
    args, kw = captured["registry_args"]
    assert args == (project, tmp_path / "home", tmp_path / "etc") and kw["model"]
    reg, skill_kw = captured["skill"]
    assert reg is captured["registry"] is seen[0].tool_context.skill_registry
    assert skill_kw["config"] is not None and skill_kw["deps"] is not None
    assert set(skill_kw["agent_definitions"]) >= {"tester", "developer"}
    send_kw = captured["send"]
    assert set(send_kw["definitions"]) >= {"tester", "developer"}
    assert "Skill" in [t.name for t in send_kw["worker_tools"]]
    assert send_kw["config"] is skill_kw["config"] and send_kw["deps"] is skill_kw["deps"]
    locations = captured["locations"]
    assert (locations.project_root, locations.home, locations.managed_dir) == (
        project, tmp_path / "home", tmp_path / "etc")


def test_the_mcp_tools_survive_a_phase_move(python_workspace, tmp_path, monkeypatch):
    """`refreshTools` re-derives the pool with the MCP tools, not the built-ins alone."""
    from app.loop.ledger import TddPhase

    monkeypatch.delenv("TDDAGENTS_COORDINATOR_MODE", raising=False)
    refreshed: list[Any] = []

    async def model(state: Any, config: Any):
        ctx = state.tool_context
        ctx.set_app_state(lambda s: dataclasses.replace(
            s, phase_ledger=dataclasses.replace(s.phase_ledger, phase=TddPhase.GREEN, red_confirmed=True)))
        refreshed.append([t.name for t in ctx.refresh_tools()])
        yield AIMessage(content="done")

    _run_mcp(python_workspace, tmp_path, model, ["mcp__demo"])
    assert "mcp__demo__echo" in refreshed[0]
