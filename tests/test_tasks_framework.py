"""Phase 8: the task model (`Task.ts`) and the registry helpers (`utils/task/framework.ts`,
`tasks/types.ts`) over `AppState.tasks`."""

from __future__ import annotations

import dataclasses

import pytest

from app.loop.context.app_state import AppState, AppStateStore
from app.loop.tasks.framework import (
    get_running_tasks,
    is_background_task,
    register_task,
    update_task_state,
)
from app.loop.tasks.task import (
    TASK_ID_ALPHABET,
    TaskStateBase,
    create_task_state_base,
    generate_task_id,
    is_terminal_task_status,
)


@pytest.mark.parametrize(
    ("task_type", "prefix"),
    [("local_bash", "b"), ("local_agent", "a"), ("remote_agent", "r"), ("in_process_teammate", "t"),
     ("local_workflow", "w"), ("monitor_mcp", "m"), ("dream", "d"), ("unknown", "x")],
)
def test_task_ids_carry_their_type_prefix(task_type, prefix):
    task_id = generate_task_id(task_type)
    assert task_id[0] == prefix and len(task_id) == 9
    assert all(c in TASK_ID_ALPHABET for c in task_id[1:])
    assert TASK_ID_ALPHABET == "0123456789abcdefghijklmnopqrstuvwxyz"


def test_task_ids_are_random():
    assert len({generate_task_id("local_agent") for _ in range(50)}) == 50


@pytest.mark.parametrize(("status", "terminal"), [("pending", False), ("running", False), ("completed", True),
                                                  ("failed", True), ("killed", True)])
def test_terminal_statuses(status, terminal):
    assert is_terminal_task_status(status) is terminal


def test_a_new_task_state_starts_pending_and_unnotified():
    base = create_task_state_base("a1", "local_agent", "desc", "/out/a1.output", tool_use_id="tu", now=5.0)
    assert base == TaskStateBase(id="a1", type="local_agent", status="pending", description="desc",
                                 tool_use_id="tu", start_time=5.0, output_file="/out/a1.output")
    assert (base.end_time, base.output_offset, base.notified) == (None, 0, False)


def _store(*tasks: TaskStateBase) -> AppStateStore:
    return AppStateStore(AppState(tasks={t.id: t for t in tasks}))


def test_register_adds_or_replaces_by_id():
    store = AppStateStore()
    first = create_task_state_base("a1", "local_agent", "one", "/o")
    register_task(first, store.update)
    assert store.get().tasks == {"a1": first}
    second = dataclasses.replace(first, description="two")
    register_task(second, store.update)
    assert store.get().tasks == {"a1": second}


def test_update_applies_the_updater_to_the_named_task_only():
    a, b = (create_task_state_base(i, "local_agent", i, "/o") for i in ("a1", "a2"))
    store = _store(a, b)
    update_task_state("a1", store.update, lambda t: dataclasses.replace(t, status="running"))
    assert store.get().tasks["a1"].status == "running"
    assert store.get().tasks["a2"] is b


def test_update_of_a_missing_task_or_a_no_op_keeps_the_state():
    a = create_task_state_base("a1", "local_agent", "d", "/o")
    store = _store(a)
    before = store.get()
    update_task_state("nope", store.update, lambda t: dataclasses.replace(t, status="running"))
    update_task_state("a1", store.update, lambda t: t)
    assert store.get() is before


def test_running_and_background_tasks():
    running = dataclasses.replace(create_task_state_base("a1", "local_agent", "d", "/o"), status="running")
    pending = create_task_state_base("a2", "local_agent", "d", "/o")
    done = dataclasses.replace(create_task_state_base("a3", "local_agent", "d", "/o"), status="completed")
    state = AppState(tasks={t.id: t for t in (running, pending, done)})
    assert get_running_tasks(state) == [running]
    assert (is_background_task(running), is_background_task(pending), is_background_task(done)) == (True, True, False)

    class Foreground:
        status = "running"
        is_backgrounded = False

    assert is_background_task(Foreground()) is False
