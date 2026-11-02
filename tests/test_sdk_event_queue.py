"""The SDK event queue (`sdkEventQueue.ts`, `sdkProgress.ts`, `sessionState.ts`)."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.bootstrap.state import SessionBootstrap
from app.cli.print import HeadlessOptions, run_headless
from app.state.app_state_store import AppState, AppStateStore
from app.utils.sdk_event_queue import (
    EMIT_SESSION_STATE_EVENTS_ENV,
    MAX_QUEUE_SIZE,
    emit_task_progress,
    emit_task_started,
    emit_task_terminated_sdk,
    enqueue_sdk_event,
    notify_session_state_changed,
)
from app.utils.task.framework import register_task
from tests.test_stream_json import Feed, Model, lines_of, run_options, user


def headless() -> AppState:
    return AppState(bootstrap=SessionBootstrap(session_id="r", is_interactive=False))


def test_only_a_non_interactive_session_queues_and_the_queue_is_bounded() -> None:
    interactive = AppState(bootstrap=SessionBootstrap(is_interactive=True))
    enqueue_sdk_event(interactive, {"subtype": "x"})
    assert interactive.sdk_events.drain("s") == []
    enqueue_sdk_event(None, {"subtype": "x"})
    state = headless()
    for i in range(MAX_QUEUE_SIZE + 1):
        enqueue_sdk_event(state, {"n": i})
    drained = state.sdk_events.drain("sess")
    assert len(drained) == MAX_QUEUE_SIZE and drained[0]["n"] == 1
    assert drained[0]["session_id"] == "sess" and drained[0]["uuid"] != drained[1]["uuid"]
    assert state.sdk_events.drain("sess") == []
    assert AppState() == AppState()  # the queue never takes part in equality


def test_the_task_events() -> None:
    state = headless()
    task = SimpleNamespace(id="t1", description="tests", type="local_agent", tool_use_id="tu", prompt=None)
    emit_task_started(state, task)
    emit_task_progress(state, task_id="t1", tool_use_id=None, description="Running", start_time=10.0,
                       total_tokens=5, tool_uses=2, last_tool_name="Bash", now=12.5)
    emit_task_terminated_sdk(state, "t1", "stopped", tool_use_id="tu", summary="tests",
                             usage={"total_tokens": 1, "tool_uses": 1, "duration_ms": 3})
    events = [{k: v for k, v in e.items() if k not in ("uuid", "session_id")} for e in state.sdk_events.drain("s")]
    assert events == [
        {"type": "system", "subtype": "task_started", "task_id": "t1", "description": "tests",
         "task_type": "local_agent", "tool_use_id": "tu"},
        {"type": "system", "subtype": "task_progress", "task_id": "t1", "description": "Running",
         "usage": {"total_tokens": 5, "tool_uses": 2, "duration_ms": 2500}, "last_tool_name": "Bash"},
        {"type": "system", "subtype": "task_notification", "task_id": "t1", "status": "stopped", "output_file": "",
         "summary": "tests", "tool_use_id": "tu", "usage": {"total_tokens": 1, "tool_uses": 1, "duration_ms": 3}},
    ]


def test_registering_a_task_announces_it_once() -> None:
    store = AppStateStore(state=headless())
    task = SimpleNamespace(id="t1", description="d", type="local_bash", status="running")
    register_task(task, store.update)
    register_task(dataclasses.replace(task) if dataclasses.is_dataclass(task) else task, store.update)
    events = store.get().sdk_events.drain("s")
    assert [e["subtype"] for e in events] == ["task_started"] and store.get().tasks["t1"] is task


def test_session_state_events_are_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    state = headless()
    notify_session_state_changed(state, "running")
    assert state.sdk_events.drain("s") == []
    monkeypatch.setenv(EMIT_SESSION_STATE_EVENTS_ENV, "1")
    notify_session_state_changed(state, "idle")
    (event,) = state.sdk_events.drain("s")
    assert (event["subtype"], event["state"]) == ("session_state_changed", "idle")


def test_the_stream_carries_session_states(python_workspace: Any, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMIT_SESSION_STATE_EVENTS_ENV, "1")
    feed = Feed(user("hi", "u1"))
    written: list[str] = []

    def write(text: str) -> None:
        written.append(text)
        if any(m.get("type") == "result" for m in lines_of([text])):
            feed.close()

    options = HeadlessOptions(output_format="stream-json", input_format="stream-json", verbose=True)
    asyncio.run(run_headless("sj-states", None, workspace=python_workspace, options=options,
                             input_blocks=feed.blocks(), write_stdout=write,
                             **run_options(tmp_path, Model([[AIMessage(content="ok")]]))))
    states = [m["state"] for m in lines_of(written) if m.get("subtype") == "session_state_changed"]
    assert states[0] == "running" and states[-1] == "idle"
