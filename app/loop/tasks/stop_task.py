"""
Stopping a task (Phase 8).

Ported from `reference/claude-code/src/tasks/stopTask.ts` -> `stopTask`, `StopTaskError`, with
`tasks.ts` -> `getTaskByType` reduced to the one task type this build runs (`local_agent`,
whose `kill` is `killAsyncAgent`). The shell-task `notified` bookkeeping has no subject here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, NamedTuple

from app.loop.context.app_state import AppState
from app.loop.tasks.framework import SetAppState
from app.loop.tasks.local_agent_task import kill_async_agent

StopTaskErrorCode = Literal["not_found", "not_running", "unsupported_type"]

#: `getTaskByType`'s `kill`, for the task types this build has.
TASK_KILLERS: dict[str, Callable[[str, SetAppState], None]] = {
    "local_agent": lambda task_id, set_app_state: kill_async_agent(task_id, set_app_state),
}


class StopTaskError(Exception):
    """`StopTaskError`."""

    def __init__(self, message: str, code: StopTaskErrorCode) -> None:
        super().__init__(message)
        self.code = code


class StopTaskResult(NamedTuple):
    task_id: str
    task_type: str
    command: str


async def stop_task(
    task_id: str, get_app_state: Callable[[], AppState], set_app_state: SetAppState
) -> StopTaskResult:
    """`stopTask`: look the task up, require it running, kill it."""
    task = get_app_state().tasks.get(task_id)
    if task is None:
        raise StopTaskError(f"No task found with ID: {task_id}", "not_found")
    if task.status != "running":
        raise StopTaskError(f"Task {task_id} is not running (status: {task.status})", "not_running")
    kill = TASK_KILLERS.get(task.type)
    if kill is None:
        raise StopTaskError(f"Unsupported task type: {task.type}", "unsupported_type")
    kill(task_id, set_app_state)
    return StopTaskResult(task_id, task.type, task.description)


__all__ = ["TASK_KILLERS", "StopTaskError", "StopTaskResult", "stop_task"]
