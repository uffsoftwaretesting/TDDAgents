"""Phase 8: background agents (`tasks/LocalAgentTask/LocalAgentTask.tsx`,
`tools/AgentTool/agentToolUtils.ts` -> `runAsyncAgentLifecycle`, `extractPartialResult`)."""

from __future__ import annotations

import asyncio
import dataclasses

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.command_queue import CommandQueue
from app.loop.context import AppState, AppStateStore, CancelToken
from app.loop.tasks.local_agent_task import (
    LocalAgentTaskState,
    complete_agent_task,
    drain_pending_messages,
    enqueue_agent_notification,
    extract_partial_result,
    fail_agent_task,
    kill_async_agent,
    queue_pending_message,
    register_async_agent,
    run_async_agent_lifecycle,
)


def _register(store: AppStateStore, task_id: str = "a1", now: float = 10.0) -> LocalAgentTaskState:
    return register_async_agent(
        agent_id=task_id, description="survey", prompt="look around", agent_type="Explore",
        set_app_state=store.update, output_file=f"/out/{task_id}.output", tool_use_id="tu1", now=now,
    )


def test_registering_starts_a_running_background_task():
    store = AppStateStore()
    task = _register(store)
    assert store.get().tasks == {"a1": task}
    assert (task.id, task.type, task.status, task.agent_id, task.agent_type) == (
        "a1", "local_agent", "running", "a1", "Explore")
    assert (task.prompt, task.description, task.output_file, task.tool_use_id, task.start_time) == (
        "look around", "survey", "/out/a1.output", "tu1", 10.0)
    assert (task.is_backgrounded, task.pending_messages, task.retrieved, task.notified) == (True, (), False, False)
    assert isinstance(task.cancel, CancelToken) and not task.cancel.cancelled


def test_pending_messages_queue_and_drain_once():
    store = AppStateStore()
    _register(store)
    queue_pending_message("a1", "first", store.update)
    queue_pending_message("a1", "second", store.update)
    assert drain_pending_messages("a1", store.get, store.update) == ["first", "second"]
    assert store.get().tasks["a1"].pending_messages == ()
    assert drain_pending_messages("a1", store.get, store.update) == []
    assert drain_pending_messages("missing", store.get, store.update) == []


def test_kill_cancels_and_marks_killed_once():
    store = AppStateStore()
    task = _register(store)
    kill_async_agent("a1", store.update, now=20.0)
    killed = store.get().tasks["a1"]
    assert (killed.status, killed.end_time) == ("killed", 20.0)
    assert task.cancel.cancelled is True
    kill_async_agent("a1", store.update, now=30.0)
    assert store.get().tasks["a1"].end_time == 20.0


def test_complete_and_fail_only_move_a_running_task():
    store = AppStateStore()
    _register(store, "a1")
    _register(store, "a2")
    complete_agent_task("a1", "the result", store.update, now=5.0)
    fail_agent_task("a2", "boom", store.update, now=6.0)
    a1, a2 = store.get().tasks["a1"], store.get().tasks["a2"]
    assert (a1.status, a1.result, a1.end_time) == ("completed", "the result", 5.0)
    assert (a2.status, a2.error, a2.end_time) == ("failed", "boom", 6.0)
    complete_agent_task("a2", "late", store.update)
    fail_agent_task("a1", "late", store.update)
    assert (store.get().tasks["a1"].status, store.get().tasks["a2"].status) == ("completed", "failed")


def test_the_notification_is_upstreams_xml_at_later_priority():
    store = AppStateStore()
    _register(store)
    queue = CommandQueue()
    enqueue_agent_notification(
        queue=queue, task_id="a1", description="survey", status="completed", set_app_state=store.update,
        output_file="/out/a1.output", final_message="found it", usage=(12, 3, 450), tool_use_id="tu1",
    )
    (command,) = queue.snapshot()
    assert (command.mode, command.priority, command.agent_id) == ("task-notification", "later", None)
    assert command.value == (
        "<task-notification>\n"
        "<task-id>a1</task-id>\n<tool-use-id>tu1</tool-use-id>\n"
        "<output-file>/out/a1.output</output-file>\n"
        "<status>completed</status>\n"
        '<summary>Agent "survey" completed</summary>\n'
        "<result>found it</result>\n"
        "<usage><total_tokens>12</total_tokens><tool_uses>3</tool_uses><duration_ms>450</duration_ms></usage>\n"
        "</task-notification>"
    )
    assert store.get().tasks["a1"].notified is True


def test_a_task_is_notified_once():
    store = AppStateStore()
    _register(store)
    queue = CommandQueue()
    for _ in range(2):
        enqueue_agent_notification(queue=queue, task_id="a1", description="d", status="killed",
                                   set_app_state=store.update, output_file="/o")
    assert len(queue) == 1


def test_failed_and_killed_summaries_and_optional_sections():
    store = AppStateStore()
    _register(store, "a1")
    _register(store, "a2")
    _register(store, "a3")
    queue = CommandQueue()
    enqueue_agent_notification(queue=queue, task_id="a1", description="d", status="failed", error="bad",
                               set_app_state=store.update, output_file="/o1")
    enqueue_agent_notification(queue=queue, task_id="a2", description="d", status="failed",
                               set_app_state=store.update, output_file="/o2")
    enqueue_agent_notification(queue=queue, task_id="a3", description="d", status="killed",
                               set_app_state=store.update, output_file="/o3")
    failed, unknown, killed = (c.value for c in queue.snapshot())
    assert '<summary>Agent "d" failed: bad</summary>' in failed
    assert '<summary>Agent "d" failed: Unknown error</summary>' in unknown
    assert '<summary>Agent "d" was stopped</summary>' in killed
    for value in (failed, unknown, killed):
        assert "<tool-use-id>" not in value and "<result>" not in value and "<usage>" not in value


def test_a_notification_for_an_unknown_task_is_dropped():
    queue = CommandQueue()
    enqueue_agent_notification(queue=queue, task_id="ghost", description="d", status="completed",
                               set_app_state=AppStateStore().update, output_file="/o")
    assert len(queue) == 0


def test_partial_result_is_the_latest_assistant_text():
    msgs = [AIMessage(content="early"), HumanMessage(content="x"), AIMessage(content=""),
            AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
            HumanMessage(content="y")]
    assert extract_partial_result(msgs) == "a\nb"
    assert extract_partial_result([HumanMessage(content="x")]) is None


async def _stream(*messages, raise_after=None, cancel=None):
    for m in messages:
        yield m
    if cancel is not None:
        cancel.cancel()
    if raise_after is not None:
        raise raise_after


def _lifecycle(store, queue, stream, cancel=None):
    task = store.get().tasks["a1"]
    times = iter([2.0, 3.0, 4.0])
    asyncio.run(run_async_agent_lifecycle(
        task_id="a1", cancel=cancel or task.cancel, stream=stream, description="survey", agent_type="Explore",
        start_time=1.0, root_set_app_state=store.update, queue=queue, output_file="/out/a1.output",
        tool_use_id="tu1", now=lambda: next(times),
    ))


def test_the_lifecycle_completes_and_notifies_with_the_result():
    store, queue = AppStateStore(), CommandQueue()
    _register(store)
    asks = AIMessage(content="", tool_calls=[{"name": "Grep", "args": {}, "id": "c1"}])
    _lifecycle(store, queue, _stream(asks, AIMessage(content="report", usage_metadata={
        "input_tokens": 7, "output_tokens": 3, "total_tokens": 10})))
    task = store.get().tasks["a1"]
    assert (task.status, task.result) == ("completed", "report")
    (note,) = queue.snapshot()
    assert "<status>completed</status>" in note.value and "<result>report</result>" in note.value
    assert "<usage><total_tokens>10</total_tokens><tool_uses>1</tool_uses><duration_ms>1000</duration_ms>" in note.value


def test_the_lifecycle_reports_a_kill_with_the_partial_result():
    store, queue = AppStateStore(), CommandQueue()
    task = _register(store)
    _lifecycle(store, queue, _stream(AIMessage(content="halfway"), cancel=task.cancel))
    assert store.get().tasks["a1"].status == "killed"
    (note,) = queue.snapshot()
    assert "<status>killed</status>" in note.value and "<result>halfway</result>" in note.value


def test_the_lifecycle_reports_a_failure():
    store, queue = AppStateStore(), CommandQueue()
    _register(store)
    _lifecycle(store, queue, _stream(raise_after=RuntimeError("provider down")))
    task = store.get().tasks["a1"]
    assert (task.status, task.error) == ("failed", "provider down")
    (note,) = queue.snapshot()
    assert '<summary>Agent "survey" failed: provider down</summary>' in note.value


def test_a_worker_without_an_answer_fails():
    store, queue = AppStateStore(), CommandQueue()
    _register(store)
    _lifecycle(store, queue, _stream(HumanMessage(content="only a prompt")))
    assert store.get().tasks["a1"].error == "No assistant messages found"


def test_the_lifecycle_without_a_queue_still_records_the_task():
    store = AppStateStore()
    _register(store)
    _lifecycle(store, None, _stream(AIMessage(content="r")))
    assert store.get().tasks["a1"].status == "completed"


def test_the_task_state_is_frozen():
    store = AppStateStore(AppState())
    task = _register(store)
    assert dataclasses.is_dataclass(task)


# ── mutation-gate pins (Phase 8) ────────────────────────────────────────────

def test_the_completed_notification_is_exact_and_timed_by_the_clock():
    store, queue = AppStateStore(), CommandQueue()
    _register(store)
    _lifecycle(store, queue, _stream(AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])))
    task = store.get().tasks["a1"]
    # finalize reads the clock first (2.0), completion second (3.0).
    assert (task.result, task.end_time) == ("a\nb", 3.0)
    (note,) = queue.snapshot()
    assert note.value == (
        "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>tu1</tool-use-id>\n"
        "<output-file>/out/a1.output</output-file>\n<status>completed</status>\n"
        '<summary>Agent "survey" completed</summary>\n<result>a\nb</result>\n'
        "<usage><total_tokens>0</total_tokens><tool_uses>0</tool_uses><duration_ms>1000</duration_ms></usage>\n"
        "</task-notification>")


def test_a_kill_and_a_failure_take_their_end_time_from_the_clock():
    store, queue = AppStateStore(), CommandQueue()
    task = _register(store)
    _lifecycle(store, queue, _stream(AIMessage(content="x"), cancel=task.cancel))
    assert store.get().tasks["a1"].end_time == 2.0
    store2 = AppStateStore()
    _register(store2)
    _lifecycle(store2, CommandQueue(), _stream(raise_after=RuntimeError("boom")))
    assert store2.get().tasks["a1"].end_time == 2.0


def test_a_notification_without_optional_parts_is_exact():
    store, queue = AppStateStore(), CommandQueue()
    _register(store, "a1")
    enqueue_agent_notification(queue=queue, task_id="a1", description="d", status="killed",
                               set_app_state=store.update, output_file="/o")
    (note,) = queue.snapshot()
    assert note.value == (
        "<task-notification>\n<task-id>a1</task-id>\n<output-file>/o</output-file>\n"
        '<status>killed</status>\n<summary>Agent "d" was stopped</summary>\n</task-notification>')


def test_killing_a_finished_or_unknown_task_changes_nothing():
    store = AppStateStore()
    task = _register(store)
    complete_agent_task("a1", "r", store.update, now=15.0)
    kill_async_agent("a1", store.update, now=20.0)
    assert task.cancel.cancelled is False
    assert (store.get().tasks["a1"].status, store.get().tasks["a1"].end_time) == ("completed", 15.0)
    kill_async_agent("ghost", store.update)
    assert set(store.get().tasks) == {"a1"}


def test_partial_text_skips_non_text_blocks_and_blank_texts():
    mixed = AIMessage(content=[{"type": "text"}, {"type": "tool_use", "text": "zz"}, {"type": "text", "text": "y"}])
    assert extract_partial_result([mixed]) == "\ny"
    assert extract_partial_result([AIMessage(content=["plain", {"type": "text", "text": "q"}])]) == "q"


def test_a_started_lifecycle_is_held_until_it_finishes():
    from app.loop.tasks import local_agent_task as lat

    async def go() -> None:
        gate = asyncio.Event()

        async def lifecycle() -> None:
            await gate.wait()

        run = lat.start_background_lifecycle(lifecycle())
        assert run in lat._BACKGROUND_RUNS
        gate.set()
        await run
        await asyncio.sleep(0)
        assert run not in lat._BACKGROUND_RUNS

    asyncio.run(go())
