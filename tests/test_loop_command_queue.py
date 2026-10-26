"""Phase 8: the run's command queue (`utils/messageQueueManager.ts`)."""

from __future__ import annotations

from typing import get_args

from app.loop.command_queue import (
    CommandQueue,
    QueuedCommand,
    QueuePriority,
    is_slash_command,
)

#: Upstream's priority levels, in order.
PRIORITIES: tuple[QueuePriority, ...] = get_args(QueuePriority)


def test_enqueue_defaults_to_next_and_notifications_to_later():
    q = CommandQueue()
    q.enqueue(QueuedCommand(value="p", mode="prompt"))
    q.enqueue_pending_notification(QueuedCommand(value="n", mode="task-notification"))
    q.enqueue_pending_notification(QueuedCommand(value="urgent", mode="task-notification", priority="now"))
    assert [(c.value, c.priority) for c in q.snapshot()] == [("p", "next"), ("n", "later"), ("urgent", "now")]


def test_dequeue_takes_the_highest_priority_fifo_within_a_level():
    q = CommandQueue()
    levels: tuple[tuple[str, QueuePriority], ...] = (("a", "later"), ("b", "next"), ("c", "next"), ("d", "now"))
    for value, priority in levels:
        q.enqueue(QueuedCommand(value=value, mode="prompt", priority=priority))
    assert [q.dequeue().value for _ in range(4)] == ["d", "b", "c", "a"]  # type: ignore[union-attr]
    assert q.dequeue() is None


def test_dequeue_and_peek_respect_a_filter():
    q = CommandQueue()
    q.enqueue(QueuedCommand(value="worker", mode="task-notification", agent_id="a1"))
    q.enqueue(QueuedCommand(value="main", mode="task-notification", priority="later"))

    def main(c: QueuedCommand) -> bool:
        return c.agent_id is None

    assert q.peek(main).value == "main"  # type: ignore[union-attr]
    assert q.peek().value == "worker"  # type: ignore[union-attr]
    assert q.dequeue(main).value == "main"  # type: ignore[union-attr]
    assert q.peek(main) is None
    assert len(q) == 1 and q.has_commands()


def test_commands_by_max_priority_and_remove():
    q = CommandQueue()
    now, nxt, later = (QueuedCommand(value=v, mode="prompt", priority=v) for v in PRIORITIES)
    for c in (later, nxt, now):
        q.enqueue(c)
    by_next = q.get_commands_by_max_priority("next")
    assert [c.value for c in by_next] == ["next", "now"]
    q.remove(by_next)
    assert [c.value for c in q.snapshot()] == ["later"]


def test_dequeue_all_matching_keeps_the_rest_in_order():
    q = CommandQueue()
    for v in ("x1", "y", "x2"):
        q.enqueue(QueuedCommand(value=v, mode="prompt"))
    assert [c.value for c in q.dequeue_all_matching(lambda c: c.value.startswith("x"))] == ["x1", "x2"]
    assert [c.value for c in q.snapshot()] == ["y"]
    assert q.dequeue_all_matching(lambda c: False) == []


def test_slash_commands():
    assert is_slash_command(QueuedCommand(value="  /commit", mode="prompt")) is True
    assert is_slash_command(QueuedCommand(value="/commit", mode="prompt", skip_slash_commands=True)) is False
    assert is_slash_command(QueuedCommand(value="hello /x", mode="prompt")) is False


def test_an_empty_queue():
    q = CommandQueue()
    assert (q.dequeue(), q.peek(), len(q), q.has_commands()) == (None, None, 0, False)
    assert q.snapshot() == ()


def test_app_state_carries_the_task_registry():
    """`appState.tasks`, empty at the start of a run."""
    from app.loop.context.app_state import AppState

    assert AppState().tasks == {}
