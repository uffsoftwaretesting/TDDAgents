"""Agent progress: the tracker, the last-tool line and the REPL's display (Part 8g)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.state.app_state_store import AppState, AppStateStore
from app.tasks.local_agent_task.local_agent_task import (
    MAX_RECENT_ACTIVITIES,
    AgentProgress,
    LocalAgentTaskState,
    ToolActivity,
    create_activity_description_resolver,
    create_progress_tracker,
    get_progress_update,
    get_token_count_from_tracker,
    run_async_agent_lifecycle,
    update_agent_progress,
    update_progress_from_message,
)
from app.tool import CancelToken
from app.tools.agent_tool.ui import extract_last_tool_info
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.grep_tool.grep_tool import build_grep_tool

TOOLS = (build_read_file_tool(), build_grep_tool(), build_bash_tool())


def _calls(*calls: tuple[str, str, dict[str, Any]], usage: dict[str, Any] | None = None) -> AIMessage:
    message = AIMessage(content="", tool_calls=[{"name": n, "id": i, "args": a} for n, i, a in calls])
    if usage is not None:
        message.usage_metadata = usage  # type: ignore[assignment]
    return message


# ── the tracker ──────────────────────────────────────────────────────────────

def test_the_tracker_keeps_the_latest_input_and_sums_outputs():
    tracker = create_progress_tracker()
    resolve = create_activity_description_resolver(TOOLS)
    update_progress_from_message(tracker, _calls(("ReadFile", "1", {"file_path": "a.py"}), usage={
        "input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
        "input_token_details": {"cache_read": 5, "cache_creation": 3}}), resolve, TOOLS)
    update_progress_from_message(tracker, _calls(("Grep", "2", {"pattern": "x"}), usage={
        "input_tokens": 150, "output_tokens": 20, "total_tokens": 170}), resolve, TOOLS)
    update_progress_from_message(tracker, HumanMessage(content="ignored"), resolve, TOOLS)
    assert (tracker.tool_use_count, tracker.latest_input_tokens, tracker.cumulative_output_tokens) == (2, 150, 30)
    assert get_token_count_from_tracker(tracker) == 180
    read, grep = tracker.recent_activities
    assert read == ToolActivity("ReadFile", {"file_path": "a.py"}, "Reading a.py", False, True)
    assert (grep.activity_description, grep.is_search, grep.is_read) == ("Searching for x", True, False)
    progress = get_progress_update(tracker)
    assert progress == AgentProgress(tool_use_count=2, token_count=180, last_activity=grep,
                                     recent_activities=(read, grep))
    assert read.as_dict() == {"toolName": "ReadFile", "input": {"file_path": "a.py"},
                              "activityDescription": "Reading a.py", "isSearch": False, "isRead": True}


def test_the_tracker_keeps_the_last_five_and_works_without_tools():
    tracker = create_progress_tracker()
    for i in range(7):
        update_progress_from_message(tracker, _calls(("Unknown", str(i), {})))
    assert tracker.tool_use_count == 7
    assert [a.tool_name for a in tracker.recent_activities] == ["Unknown"] * MAX_RECENT_ACTIVITIES
    assert tracker.recent_activities[0].input == {} and tracker.recent_activities[-1].is_search is None
    assert get_progress_update(create_progress_tracker()) == AgentProgress(tool_use_count=0, token_count=0)
    assert create_activity_description_resolver(TOOLS)("Nope", {}) is None


def test_only_a_running_task_takes_progress_and_its_summary_stays():
    store = AppStateStore()
    running = LocalAgentTaskState(id="a1", type="local_agent", status="running", description="d",
                                  output_file="", start_time=0.0, agent_id="a1")
    store.update(lambda s: replace(s, tasks={"a1": running, "a2": replace(running, id="a2", status="completed")}))
    progress = AgentProgress(tool_use_count=1, token_count=5)
    update_agent_progress("a1", progress, store.update)
    update_agent_progress("a2", progress, store.update)
    tasks = store.get().tasks
    assert tasks["a1"].progress == progress and tasks["a2"].progress is None
    summarized = replace(tasks["a1"], progress=replace(progress, summary="Writing tests"))
    store.update(lambda s: replace(s, tasks={**s.tasks, "a1": summarized}))
    update_agent_progress("a1", AgentProgress(tool_use_count=2, token_count=9), store.update)
    assert store.get().tasks["a1"].progress == AgentProgress(
        tool_use_count=2, token_count=9, summary="Writing tests")


def test_a_background_agent_updates_its_progress_per_message():
    store = AppStateStore()
    task = LocalAgentTaskState(id="b1", type="local_agent", status="running", description="d",
                               output_file="", start_time=0.0, agent_id="b1")
    store.update(lambda s: replace(s, tasks={"b1": task}))
    seen: list[Any] = []
    store.subscribe(lambda: seen.append(getattr(store.get().tasks["b1"], "progress", None)))

    async def stream() -> Any:
        yield _calls(("ReadFile", "1", {"file_path": "x.py"}))
        yield ToolMessage(content="ok", tool_call_id="1")
        yield AIMessage(content="done")

    asyncio.run(run_async_agent_lifecycle(
        task_id="b1", cancel=CancelToken(), stream=stream(), description="d", agent_type="general",
        start_time=0.0, root_set_app_state=store.update, queue=None, output_file="/dev/null",
        now=lambda: 1.0, tools=TOOLS))
    progressed = [p for p in seen if p is not None]
    assert progressed[0].tool_use_count == 1
    assert progressed[0].last_activity.activity_description == "Reading x.py"


def test_a_background_agent_reports_task_progress_to_the_sdk():
    """`runAsyncAgentLifecycle` -> `emitTaskProgress`: after each message that calls a tool."""
    from app.bootstrap.state import SessionBootstrap
    from app.tool import tool_context_for

    store = AppStateStore(state=replace(AppStateStore().get(), bootstrap=SessionBootstrap(is_interactive=False)))
    task = LocalAgentTaskState(id="b1", type="local_agent", status="running", description="d",
                               output_file="", start_time=0.0, agent_id="b1")
    store.update(lambda s: replace(s, tasks={"b1": task}))

    async def stream() -> Any:
        yield _calls(("ReadFile", "1", {"file_path": "x.py"}))
        yield ToolMessage(content="ok", tool_call_id="1")
        yield AIMessage(content="done")

    asyncio.run(run_async_agent_lifecycle(
        task_id="b1", cancel=CancelToken(), stream=stream(), description="d", agent_type="general",
        start_time=0.0, root_set_app_state=store.update, queue=None, output_file="/dev/null", tool_use_id="tu",
        now=lambda: 2.0, tools=TOOLS, context=tool_context_for(store)))
    (event,) = [e for e in store.get().sdk_events.drain("s") if e["subtype"] == "task_progress"]
    assert (event["task_id"], event["tool_use_id"], event["description"], event["last_tool_name"]) == (
        "b1", "tu", "Reading x.py", "ReadFile")
    assert event["usage"] == {"total_tokens": 0, "tool_uses": 1, "duration_ms": 2000}


# ── extractLastToolInfo ──────────────────────────────────────────────────────

def test_the_last_finished_tool_with_its_summary():
    messages = [_calls(("Grep", "1", {"pattern": "def main"})), ToolMessage(content="x", tool_call_id="1")]
    assert extract_last_tool_info(messages, TOOLS) == "Search: def main"
    unfinished = [_calls(("Grep", "1", {"pattern": "x"}))]
    assert extract_last_tool_info(unfinished, TOOLS) is None
    assert extract_last_tool_info([], TOOLS) is None
    unknown = [_calls(("Mystery", "9", {})), ToolMessage(content="x", tool_call_id="9")]
    assert extract_last_tool_info(unknown, TOOLS) == "Mystery"
    orphan = [ToolMessage(content="x", tool_call_id="zz")]
    assert extract_last_tool_info(orphan, TOOLS) is None
    no_summary = [_calls(("ReadFile", "1", {})), ToolMessage(content="x", tool_call_id="1")]
    assert extract_last_tool_info(no_summary, TOOLS) == "Read"


def test_trailing_searches_and_reads_roll_up():
    messages = [AIMessage(content="looking"),
                _calls(("Grep", "1", {"pattern": "a"})), ToolMessage(content="x", tool_call_id="1"),
                _calls(("ReadFile", "2", {"file_path": "b.py"})), ToolMessage(content="x", tool_call_id="2"),
                _calls(("Grep", "3", {"pattern": "c"})), ToolMessage(content="x", tool_call_id="3")]
    assert extract_last_tool_info(messages, TOOLS) == "Searching for 2 patterns, reading 1 file…"
    broken = [_calls(("Grep", "1", {"pattern": "a"})), ToolMessage(content="x", tool_call_id="1"),
              _calls(("Bash", "2", {"command": "ls"})), ToolMessage(content="x", tool_call_id="2")]
    assert extract_last_tool_info(broken, TOOLS) == "Bash: ls"


# ── the store's subscribers ──────────────────────────────────────────────────

def test_store_subscribers_hear_changes_only():
    store = AppStateStore()
    heard: list[int] = []
    unsubscribe = store.subscribe(lambda: heard.append(1))
    store.update(lambda s: s)
    store.update(lambda s: replace(s, tdd_paused=True))
    assert heard == [1]
    unsubscribe()
    unsubscribe()
    store.update(lambda s: replace(s, tdd_paused=False))
    assert heard == [1]
    assert AppStateStore(AppState()).listeners == []


# ── the REPL ─────────────────────────────────────────────────────────────────

def _repl() -> tuple[Any, list[str], AppStateStore]:
    from app.screens.repl import Repl

    written: list[str] = []
    store = AppStateStore()
    ctx = SimpleNamespace(tools=TOOLS, command_queue=None)
    run = SimpleNamespace(ctx=ctx, store=store, on_message=None)
    repl = Repl(run, SimpleNamespace(write=written.append))  # type: ignore[arg-type]
    return repl, written, store


def test_the_repl_shows_a_foreground_workers_last_tool():
    repl, written, _ = _repl()
    assert repl.run.ctx.on_progress == repl._on_progress
    repl._on_progress("t1", {"type": "agent_progress", "message": _calls(("Grep", "1", {"pattern": "x"}))})
    repl._on_progress("t1", {"type": "agent_progress", "message": ToolMessage(content="ok", tool_call_id="1")})
    repl._on_progress("t1", {"type": "agent_progress", "message": AIMessage(content="thinking")})
    repl._on_progress("t1", {"type": "bash_progress"})
    assert written == ["  ⎿  Search: x"]


def test_the_repl_shows_a_background_agents_activity():
    _, written, store = _repl()
    activity = ToolActivity("ReadFile", {}, "Reading a.py", False, True)
    task = LocalAgentTaskState(id="b1", type="local_agent", status="running", description="Explore",
                               output_file="", start_time=0.0,
                               agent_id="b1", progress=AgentProgress(1, 5, activity, (activity,)))
    store.update(lambda s: replace(s, tasks={"b1": task}))
    store.update(lambda s: replace(s, tdd_paused=True))  # unchanged activity: nothing new
    second = ToolActivity("Grep", {}, "Searching for x", True, False)
    store.update(lambda s: replace(s, tasks={"b1": replace(task, progress=AgentProgress(
        2, 9, second, (activity, second)))}))
    done = replace(task, status="completed")
    store.update(lambda s: replace(s, tasks={"b1": done, "x": replace(task, id="x", type="local_bash")}))
    assert written == ["  ⎿  Explore: Reading a.py", "  ⎿  Explore: Searching for 1 pattern, reading 1 file…"]
    quiet = replace(task, id="q", progress=AgentProgress(0, 0))
    store.update(lambda s: replace(s, tasks={"q": quiet}))
    assert len(written) == 2
