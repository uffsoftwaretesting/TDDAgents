"""
The task model (Phase 8).

Ported from `reference/claude-code/src/Task.ts` -> `TaskType`, `TaskStatus`,
`isTerminalTaskStatus`, `TaskStateBase`, `generateTaskId`, `createTaskStateBase`. A task is a
unit of background work recorded in `AppState.tasks` by id; this build has one kind, the
background agent (`local_agent`, `tasks/local_agent_task/local_agent_task.py`), but keeps upstream's whole
vocabulary so ids and statuses read the same.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal

TaskType = Literal[
    "local_bash", "local_agent", "remote_agent", "in_process_teammate", "local_workflow", "monitor_mcp", "dream",
]

TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]

#: `TASK_ID_PREFIXES` (`local_bash` keeps `b` for backward compatibility upstream).
TASK_ID_PREFIXES: dict[str, str] = {
    "local_bash": "b",
    "local_agent": "a",
    "remote_agent": "r",
    "in_process_teammate": "t",
    "local_workflow": "w",
    "monitor_mcp": "m",
    "dream": "d",
}

#: `TASK_ID_ALPHABET`.
TASK_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def is_terminal_task_status(status: str) -> bool:
    """`isTerminalTaskStatus`: completed, failed and killed tasks never transition again."""
    return status in ("completed", "failed", "killed")


def generate_task_id(task_type: str) -> str:
    """`generateTaskId`: the type's prefix plus 8 characters from 8 random bytes."""
    prefix = TASK_ID_PREFIXES.get(task_type, "x")
    return prefix + "".join(TASK_ID_ALPHABET[b % len(TASK_ID_ALPHABET)] for b in secrets.token_bytes(8))


@dataclass(frozen=True)
class TaskStateBase:
    """`TaskStateBase` (no `totalPausedMs`: nothing here pauses a task)."""

    id: str
    type: str
    status: str
    description: str
    output_file: str
    start_time: float
    tool_use_id: str | None = None
    end_time: float | None = None
    output_offset: int = 0
    notified: bool = False


def create_task_state_base(
    task_id: str,
    task_type: str,
    description: str,
    output_file: str,
    *,
    tool_use_id: str | None = None,
    now: float | None = None,
) -> TaskStateBase:
    """`createTaskStateBase`: pending, unnotified, output offset 0."""
    return TaskStateBase(
        id=task_id,
        type=task_type,
        status="pending",
        description=description,
        tool_use_id=tool_use_id,
        start_time=time.time() if now is None else now,
        output_file=output_file,
    )


__all__ = [
    "TASK_ID_ALPHABET",
    "TASK_ID_PREFIXES",
    "TaskStateBase",
    "TaskStatus",
    "TaskType",
    "create_task_state_base",
    "generate_task_id",
    "is_terminal_task_status",
]
