"""
Stopping a task (Phase 8).

Ported from `reference/claude-code/src/tasks/stopTask.ts` -> `stopTask`, `StopTaskError`, with
`tasks.ts` -> `getTaskByType` reduced to the task types this build runs: `local_bash`
(`killTask`) and `local_agent` (`killAsyncAgent`). A stopped shell is marked notified, so no
"exit 137" notification follows the stop, and the result names its command.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, NamedTuple

from app.state.app_state_store import AppState
from app.tasks.local_agent_task.local_agent_task import kill_async_agent
from app.tasks.local_shell_task.kill_shell_tasks import kill_task
from app.utils.task.framework import SetAppState

StopTaskErrorCode = Literal["not_found", "not_running", "unsupported_type"]

#: `getTaskByType`'s `kill`, for the task types this build has.
TASK_KILLERS: dict[str, Callable[[str, SetAppState], None]] = {
    "local_bash": kill_task,
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
    if task.type == "local_bash":
        # `kill_task` already set `notified`; upstream suppresses the "exit 137" notification here.
        return StopTaskResult(task_id, task.type, getattr(task, "command", "") or task.description)
    return StopTaskResult(task_id, task.type, task.description)


__all__ = ["TASK_KILLERS", "StopTaskError", "StopTaskResult", "stop_task"]
