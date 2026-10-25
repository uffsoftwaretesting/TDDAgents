"""
The run's command queue (Phase 8).

Ported from `reference/claude-code/src/utils/messageQueueManager.ts` (`enqueue`,
`enqueuePendingNotification`, `dequeue`, `peek`, `dequeueAllMatching`, `remove`,
`getCommandsByMaxPriority`, `isSlashCommand`). Upstream's queue is a process-global singleton
shared by the coordinator and every in-process worker; here it is one object per run, carried
on `ToolContext.command_queue` and shared by reference with every worker, which is the same
sharing.

What feeds it: background-task notifications (`enqueue_agent_notification`) and `asyncRewake`
hook notifications, both at `later` priority so they never starve a prompt. What drains it:
the `queued_commands` attachment source between tool rounds (`next` and above, scoped by
agent), and the runner between turns (`print.ts`'s `waitingForAgents` loop), which is where a
`later` notification becomes a new turn.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

logger = logging.getLogger(__name__)

QueuePriority = Literal["now", "next", "later"]

PRIORITY_ORDER: dict[str, int] = {"now": 0, "next": 1, "later": 2}


@dataclass(frozen=True)
class QueuedCommand:
    """`QueuedCommand`: the fields a headless run uses."""

    value: str
    mode: str
    priority: QueuePriority | None = None
    uuid: str = field(default_factory=lambda: str(uuid_module.uuid4()))
    #: The worker a notification is addressed to; `None` is the main thread.
    agent_id: str | None = None
    skip_slash_commands: bool = False


def _priority(command: QueuedCommand) -> int:
    return PRIORITY_ORDER[command.priority or "next"]


def is_slash_command(command: QueuedCommand) -> bool:
    """`isSlashCommand`."""
    return command.value.strip().startswith("/") and not command.skip_slash_commands


class CommandQueue:
    """One run's queue. Not thread-safe: the loop and its workers share one event loop."""

    def __init__(self) -> None:
        self._commands: list[QueuedCommand] = []

    def __len__(self) -> int:
        return len(self._commands)

    def has_commands(self) -> bool:
        """`hasCommandsInQueue`."""
        return bool(self._commands)

    def snapshot(self) -> tuple[QueuedCommand, ...]:
        """`getCommandQueueSnapshot`."""
        return tuple(self._commands)

    def enqueue(self, command: QueuedCommand) -> None:
        """`enqueue`: priority defaults to `next`."""
        self._commands.append(replace(command, priority=command.priority or "next"))

    def enqueue_pending_notification(self, command: QueuedCommand) -> None:
        """`enqueuePendingNotification`: priority defaults to `later`, so input is never starved."""
        self._commands.append(replace(command, priority=command.priority or "later"))

    def _best(self, filter: Callable[[QueuedCommand], bool] | None) -> int:
        best_index, best_priority = -1, len(PRIORITY_ORDER)
        for index, command in enumerate(self._commands):
            if filter is not None and not filter(command):
                continue
            if _priority(command) < best_priority:
                best_index, best_priority = index, _priority(command)
        return best_index

    def dequeue(self, filter: Callable[[QueuedCommand], bool] | None = None) -> QueuedCommand | None:
        """`dequeue`: the highest-priority command passing `filter`, FIFO within a level."""
        index = self._best(filter)
        return None if index == -1 else self._commands.pop(index)

    def peek(self, filter: Callable[[QueuedCommand], bool] | None = None) -> QueuedCommand | None:
        """`peek`: as `dequeue`, without removing."""
        index = self._best(filter)
        return None if index == -1 else self._commands[index]

    def dequeue_all_matching(self, predicate: Callable[[QueuedCommand], bool]) -> list[QueuedCommand]:
        """`dequeueAllMatching`: every match, in queue order; the rest stays."""
        matched = [c for c in self._commands if predicate(c)]
        if matched:
            self._commands = [c for c in self._commands if not predicate(c)]
        return matched

    def remove(self, commands: Iterable[QueuedCommand]) -> None:
        """`remove`: by identity."""
        doomed = {id(c) for c in commands}
        self._commands = [c for c in self._commands if id(c) not in doomed]

    def get_commands_by_max_priority(self, max_priority: QueuePriority) -> list[QueuedCommand]:
        """`getCommandsByMaxPriority`: every command at or above `max_priority`, in queue order."""
        threshold = PRIORITY_ORDER[max_priority]
        return [c for c in self._commands if _priority(c) <= threshold]


__all__ = ["PRIORITY_ORDER", "CommandQueue", "QueuePriority", "QueuedCommand", "is_slash_command"]
