"""
Shell commands that run in the background, and their one completion notification.

Ported from `reference/claude-code/src/tasks/LocalShellTask/LocalShellTask.tsx` ->
`spawnShellTask`, `registerForeground`, `backgroundExistingForegroundTask`,
`unregisterForeground`, `markTaskNotified`, `enqueueShellNotification`, `startStallWatchdog`,
`looksLikePrompt`, `BACKGROUND_BASH_SUMMARY_PREFIX`. A background shell is a `local_bash` task in
`AppState.tasks`; when it ends, one `<task-notification>` is enqueued at `later` priority on the
run's command queue (upstream's priority without the Monitor tool) and the runner delivers it.
Its output file is the one `exec_shell` opened, so a `ReadFile` of the path in the tool result
shows the output as it grows.

Not ported: the Monitor kind, `backgroundAll` (Ctrl+B) and the speculation abort.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any

from app.tasks.local_agent_task.local_agent_task import (
    OUTPUT_FILE_TAG,
    STATUS_TAG,
    SUMMARY_TAG,
    TASK_ID_TAG,
    TASK_NOTIFICATION_TAG,
    TOOL_USE_ID_TAG,
)
from app.tasks.local_shell_task.guards import LocalShellTaskState
from app.utils.message_queue_manager import QueuedCommand
from app.utils.task.framework import SetAppState, register_task, update_task_state
from app.utils.xml import escape_xml

#: `BACKGROUND_BASH_SUMMARY_PREFIX`.
BACKGROUND_BASH_SUMMARY_PREFIX = "Background command "
#: `STALL_CHECK_INTERVAL_MS` / `STALL_THRESHOLD_MS` (seconds) and `STALL_TAIL_BYTES`.
STALL_CHECK_INTERVAL_SECONDS = 5.0
STALL_THRESHOLD_SECONDS = 45.0
STALL_TAIL_BYTES = 1024

#: `PROMPT_PATTERNS`: last lines that look like a command waiting for keyboard input.
PROMPT_PATTERNS = (
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(r"\b(?:Do you|Would you|Shall I|Are you sure|Ready to)\b.*\? *$", re.IGNORECASE),
    re.compile(r"Press (any key|Enter)", re.IGNORECASE),
    re.compile(r"Continue\?", re.IGNORECASE),
    re.compile(r"Overwrite\?", re.IGNORECASE),
)

#: Running completion handlers and watchdogs, held so the loop does not drop them.
_RUNNING: set[asyncio.Task[Any]] = set()


def _keep(coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    task: asyncio.Task[Any] = asyncio.ensure_future(coroutine)
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return task


def looks_like_prompt(tail: str) -> bool:
    """`looksLikePrompt`: the last line matches one of `PROMPT_PATTERNS`."""
    last_line = tail.rstrip().split("\n")[-1]
    return any(pattern.search(last_line) for pattern in PROMPT_PATTERNS)


def _tail(path: str, max_bytes: int) -> str:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")


async def _stall_watchdog(context: Any, task_id: str, description: str, output_file: str,
                          tool_use_id: str | None, agent_id: str | None) -> None:
    """
    `startStallWatchdog`: when the output stops growing for `STALL_THRESHOLD_SECONDS` and its last
    line looks like a prompt, enqueue one notification (no `<status>`: the task is still running)
    telling the model the command is likely blocked on interactive input.
    """
    last_size, last_growth = 0, time.monotonic()
    while True:
        await asyncio.sleep(STALL_CHECK_INTERVAL_SECONDS)
        try:
            size = os.path.getsize(output_file)
        except OSError:
            continue
        if size > last_size:
            last_size, last_growth = size, time.monotonic()
            continue
        if time.monotonic() - last_growth < STALL_THRESHOLD_SECONDS:
            continue
        try:
            content = _tail(output_file, STALL_TAIL_BYTES)
        except OSError:
            continue
        if not looks_like_prompt(content):
            last_growth = time.monotonic()
            continue
        queue = getattr(context, "command_queue", None)
        if queue is not None:
            tool_use_line = f"\n<{TOOL_USE_ID_TAG}>{tool_use_id}</{TOOL_USE_ID_TAG}>" if tool_use_id else ""
            summary = f'{BACKGROUND_BASH_SUMMARY_PREFIX}"{description}" appears to be waiting for interactive input'
            message = (
                f"<{TASK_NOTIFICATION_TAG}>\n"
                f"<{TASK_ID_TAG}>{task_id}</{TASK_ID_TAG}>{tool_use_line}\n"
                f"<{OUTPUT_FILE_TAG}>{output_file}</{OUTPUT_FILE_TAG}>\n"
                f"<{SUMMARY_TAG}>{escape_xml(summary)}</{SUMMARY_TAG}>\n"
                f"</{TASK_NOTIFICATION_TAG}>\n"
                f"Last output:\n{content.rstrip()}\n\n"
                "The command is likely blocked on an interactive prompt. Kill this task and re-run with piped "
                "input (e.g., `echo y | command`) or a non-interactive flag if one exists."
            )
            queue.enqueue_pending_notification(QueuedCommand(value=message, mode="task-notification",
                                                             priority="next", agent_id=agent_id))
        return


def enqueue_shell_notification(
    context: Any, task_id: str, description: str, status: str, exit_code: int | None,
    output_file: str, tool_use_id: str | None = None, agent_id: str | None = None,
) -> None:
    """`enqueueShellNotification`: claim `notified`, then enqueue upstream's XML (once)."""
    claimed = {"ok": False}

    def claim(task: Any) -> Any:
        if task.notified:
            return task
        claimed["ok"] = True
        return replace(task, notified=True)

    update_task_state(task_id, context.set_app_state, claim)
    queue = getattr(context, "command_queue", None)
    if not claimed["ok"] or queue is None:
        return
    if status == "completed":
        code = f" (exit code {exit_code})" if exit_code is not None else ""
        summary = f'{BACKGROUND_BASH_SUMMARY_PREFIX}"{description}" completed{code}'
    elif status == "failed":
        code = f" with exit code {exit_code}" if exit_code is not None else ""
        summary = f'{BACKGROUND_BASH_SUMMARY_PREFIX}"{description}" failed{code}'
    else:
        summary = f'{BACKGROUND_BASH_SUMMARY_PREFIX}"{description}" was stopped'
    tool_use_line = f"\n<{TOOL_USE_ID_TAG}>{tool_use_id}</{TOOL_USE_ID_TAG}>" if tool_use_id else ""
    message = (
        f"<{TASK_NOTIFICATION_TAG}>\n"
        f"<{TASK_ID_TAG}>{task_id}</{TASK_ID_TAG}>{tool_use_line}\n"
        f"<{OUTPUT_FILE_TAG}>{output_file}</{OUTPUT_FILE_TAG}>\n"
        f"<{STATUS_TAG}>{status}</{STATUS_TAG}>\n"
        f"<{SUMMARY_TAG}>{escape_xml(summary)}</{SUMMARY_TAG}>\n"
        f"</{TASK_NOTIFICATION_TAG}>"
    )
    queue.enqueue_pending_notification(QueuedCommand(value=message, mode="task-notification", agent_id=agent_id))


def _watch_completion(context: Any, shell_command: Any, task_id: str, description: str, output_file: str,
                      tool_use_id: str | None, agent_id: str | None, stall: asyncio.Task[Any]) -> None:
    """The handler `spawnShellTask` / `backgroundExistingForegroundTask` attach to the result."""

    async def watch() -> None:
        result = await shell_command.result
        stall.cancel()
        shell_command.cleanup()
        was_killed = {"value": False}

        def finish(task: Any) -> Any:
            if task.status == "killed":
                was_killed["value"] = True
                return task
            return replace(task, status="completed" if result.code == 0 else "failed",
                           result=(result.code, result.interrupted), shell_command=None, end_time=time.time())

        update_task_state(task_id, context.set_app_state, finish)
        status = "killed" if was_killed["value"] else "completed" if result.code == 0 else "failed"
        enqueue_shell_notification(context, task_id, description, status, result.code, output_file,
                                   tool_use_id, agent_id)

    _keep(watch())


def spawn_shell_task(
    context: Any, *, command: str, description: str, shell_command: Any,
    tool_use_id: str | None = None, agent_id: str | None = None,
) -> str:
    """`spawnShellTask`: register a running, backgrounded task and detach the command."""
    task_id: str = shell_command.task_output.task_id
    output_file = str(shell_command.task_output.path)
    register_task(LocalShellTaskState(
        id=task_id, type="local_bash", status="running", description=description, output_file=output_file,
        start_time=time.time(), tool_use_id=tool_use_id, command=command, shell_command=shell_command,
        is_backgrounded=True, agent_id=agent_id,
    ), context.set_app_state)
    shell_command.background(task_id)
    stall = _keep(_stall_watchdog(context, task_id, description, output_file, tool_use_id, agent_id))
    _watch_completion(context, shell_command, task_id, description, output_file, tool_use_id, agent_id, stall)
    return task_id


def register_foreground(
    context: Any, *, command: str, description: str, shell_command: Any,
    tool_use_id: str | None = None, agent_id: str | None = None,
) -> str:
    """`registerForeground`: a task the Bash call still waits on (it can be backgrounded later)."""
    task_id: str = shell_command.task_output.task_id
    register_task(LocalShellTaskState(
        id=task_id, type="local_bash", status="running", description=description,
        output_file=str(shell_command.task_output.path), start_time=time.time(), tool_use_id=tool_use_id,
        command=command, shell_command=shell_command, is_backgrounded=False, agent_id=agent_id,
    ), context.set_app_state)
    return task_id


def background_existing_foreground_task(
    context: Any, task_id: str, shell_command: Any, description: str, tool_use_id: str | None = None,
) -> bool:
    """`backgroundExistingForegroundTask`: flip a registered foreground task to the background."""
    task = context.get_app_state().tasks.get(task_id)
    if task is None or task.is_backgrounded or not shell_command.background(task_id):
        return False
    update_task_state(task_id, context.set_app_state, lambda t: replace(t, is_backgrounded=True))
    agent_id = getattr(task, "agent_id", None)
    stall = _keep(_stall_watchdog(context, task_id, description, task.output_file, tool_use_id, agent_id))
    _watch_completion(context, shell_command, task_id, description, task.output_file, tool_use_id, agent_id, stall)
    return True


def mark_task_notified(task_id: str, set_app_state: SetAppState) -> None:
    """`markTaskNotified`: a result the Bash call returns itself needs no notification."""
    update_task_state(task_id, set_app_state, lambda t: t if t.notified else replace(t, notified=True))


def unregister_foreground(task_id: str, set_app_state: SetAppState) -> None:
    """`unregisterForeground`: a foreground task that finished in the call is dropped."""

    def drop(state: Any) -> Any:
        task = state.tasks.get(task_id)
        if task is None or task.is_backgrounded:
            return state
        return replace(state, tasks={k: v for k, v in state.tasks.items() if k != task_id})

    set_app_state(drop)
