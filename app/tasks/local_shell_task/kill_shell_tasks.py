"""
Stopping shell tasks.

Ported from `reference/claude-code/src/tasks/LocalShellTask/killShellTasks.ts` -> `killTask`,
`killShellTasksForAgent`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.state.app_state_store import AppState
from app.utils.task.framework import SetAppState, update_task_state


def kill_task(task_id: str, set_app_state: SetAppState) -> None:
    """`killTask`: kill a running shell task; it is `killed` and already notified."""

    def kill(task: Any) -> Any:
        if task.status != "running" or task.type != "local_bash":
            return task
        if task.shell_command is not None:
            task.shell_command.kill()
            task.shell_command.cleanup()
        return replace(task, status="killed", notified=True, shell_command=None, end_time=time.time())

    update_task_state(task_id, set_app_state, kill)


def kill_shell_tasks_for_agent(
    agent_id: str, get_app_state: Callable[[], AppState], set_app_state: SetAppState, queue: Any = None,
) -> None:
    """
    `killShellTasksForAgent`: when an agent ends, its running shells are killed and the
    notifications queued for it are dropped (nobody is left to read them).
    """
    for task_id, task in list(get_app_state().tasks.items()):
        if task.type == "local_bash" and task.status == "running" and getattr(task, "agent_id", None) == agent_id:
            kill_task(task_id, set_app_state)
    if queue is not None:
        queue.dequeue_all_matching(lambda command: command.agent_id == agent_id)


def kill_all_shell_tasks(get_app_state: Callable[[], AppState], set_app_state: SetAppState) -> None:
    """The session's teardown (upstream's `registerCleanup` per task): kill every running shell."""
    for task_id, task in list(get_app_state().tasks.items()):
        if task.type == "local_bash" and task.status == "running":
            kill_task(task_id, set_app_state)
