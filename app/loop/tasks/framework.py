"""
The task registry over `AppState.tasks` (Phase 8).

Ported from `reference/claude-code/src/utils/task/framework.ts` -> `updateTaskState`,
`registerTask`, `getRunningTasks`, and `tasks/types.ts` -> `isBackgroundTask`. Every write goes
through a `set_app_state` updater, so a task changes only as a replacement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.loop.context import AppState

SetAppState = Callable[[Callable[[AppState], AppState]], None]


def update_task_state(task_id: str, set_app_state: SetAppState, updater: Callable[[Any], Any]) -> None:
    """`updateTaskState`: a missing task, or an updater returning the same object, changes nothing."""

    def apply(state: AppState) -> AppState:
        task = state.tasks.get(task_id)
        if task is None:
            return state
        updated = updater(task)
        if updated is task:
            return state
        return replace(state, tasks={**state.tasks, task_id: updated})

    set_app_state(apply)


def register_task(task: Any, set_app_state: SetAppState) -> None:
    """`registerTask`: add the task, or replace the one with its id (a resumed agent)."""
    set_app_state(lambda state: replace(state, tasks={**state.tasks, task.id: task}))


def get_running_tasks(state: AppState) -> list[Any]:
    """`getRunningTasks`."""
    return [task for task in state.tasks.values() if task.status == "running"]


def is_background_task(task: Any) -> bool:
    """`isBackgroundTask`: running or pending, and not a foreground task."""
    if task.status not in ("running", "pending"):
        return False
    return getattr(task, "is_backgrounded", True) is not False


__all__ = ["SetAppState", "get_running_tasks", "is_background_task", "register_task", "update_task_state"]
