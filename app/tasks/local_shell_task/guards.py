"""
A background (or foreground-registered) shell command's task record.

Ported from `reference/claude-code/src/tasks/LocalShellTask/guards.ts` -> `LocalShellTaskState`,
`isLocalShellTask`. Not ported: `unregisterCleanup` and `cleanupTimeoutId` (the session's
teardown kills running shells directly) and `lastReportedTotalLines` (UI progress).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.task import TaskStateBase


@dataclass(frozen=True)
class LocalShellTaskState(TaskStateBase):
    """`LocalShellTaskState`."""

    command: str = ""
    #: `{code, interrupted}` once the command ended.
    result: tuple[int, bool] | None = None
    completion_status_sent_in_attachment: bool = False
    #: The live `ShellCommand`; `None` once the command ended.
    shell_command: Any = None
    #: `False` while the Bash call that started it still waits on it (a foreground task).
    is_backgrounded: bool = True
    agent_id: str | None = None
    kind: str = "bash"


def is_local_shell_task(task: Any) -> bool:
    """`isLocalShellTask`."""
    return getattr(task, "type", None) == "local_bash"
