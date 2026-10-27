"""Phase 8: the `queued_commands` source over the run's command queue (`query.ts`: the
`getCommandsByMaxPriority('next')` snapshot, scoped by agent, removed once consumed)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from langchain_core.messages import HumanMessage

from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.utils.attachments import get_queued_command_attachments
from app.utils.message_queue_manager import CommandQueue, QueuedCommand


def _ctx(queue: CommandQueue, agent_id: str | None = None):
    return replace(tool_context_for(AppStateStore()), command_queue=queue, agent_id=agent_id)


def _drain(ctx):
    return get_queued_command_attachments([HumanMessage(content="t")], ctx)


def test_the_main_thread_takes_next_priority_commands_addressed_to_it():
    q = CommandQueue()
    q.enqueue(QueuedCommand(value="steer", mode="prompt"))
    q.enqueue(QueuedCommand(value="for a1", mode="task-notification", agent_id="a1"))
    q.enqueue_pending_notification(QueuedCommand(value="later", mode="task-notification"))
    steer = q.snapshot()[0]
    got = _drain(_ctx(q))
    assert [(a.type, a.data) for a in got] == [
        ("queued_command", {"prompt": "steer", "source_uuid": steer.uuid, "commandMode": "prompt"})]
    # Consumed commands leave the queue; the later notification waits for a turn of its own.
    assert [c.value for c in q.snapshot()] == ["for a1", "later"]


def test_a_worker_takes_only_its_own_task_notifications():
    q = CommandQueue()
    q.enqueue(QueuedCommand(value="prompt for a1", mode="prompt", agent_id="a1"))
    q.enqueue(QueuedCommand(value="note a1", mode="task-notification", agent_id="a1", priority="next"))
    q.enqueue(QueuedCommand(value="note a2", mode="task-notification", agent_id="a2", priority="next"))
    got = _drain(_ctx(q, agent_id="a1"))
    assert [a.data["prompt"] for a in got] == ["note a1"]
    assert [c.value for c in q.snapshot()] == ["prompt for a1", "note a2"]


def test_slash_commands_and_non_inline_modes_are_left_alone():
    """`INLINE_NOTIFICATION_MODES`: only prompts and task notifications become attachments."""
    q = CommandQueue()
    q.enqueue(QueuedCommand(value="/compact", mode="prompt"))
    q.enqueue(QueuedCommand(value="orphan", mode="orphaned-permission"))
    assert _drain(_ctx(q)) == []
    assert [c.value for c in q.snapshot()] == ["/compact", "orphan"]


def test_no_queue_means_nothing():
    assert _drain(tool_context_for(AppStateStore())) == []


def test_the_hook_registry_feeds_the_queue_at_later_priority():
    from app.utils.hooks.async_hook_registry import AsyncHookRegistry

    q = CommandQueue()
    registry = AsyncHookRegistry(queue=q)
    assert registry.queue is q
    assert AsyncHookRegistry().queue is not q

    class Capture:
        stderr, stdout = "bad", ""

        async def result(self):
            return 2

    async def go():
        registry.register_rewake(hook_name="Stop", capture=Capture())  # type: ignore[arg-type]
        for _ in range(50):
            await asyncio.sleep(0)
            if q.has_commands():
                break

    asyncio.run(go())
    (command,) = q.snapshot()
    assert (command.mode, command.priority, command.agent_id) == ("task-notification", "later", None)
    assert 'Stop hook blocking error from command "Stop": bad' in command.value
