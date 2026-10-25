"""
Background agents (Phase 8).

Ported from `reference/claude-code/src/tasks/LocalAgentTask/LocalAgentTask.tsx` ->
`LocalAgentTaskState`, `registerAsyncAgent`, `queuePendingMessage`, `drainPendingMessages`,
`enqueueAgentNotification`, `killAsyncAgent`, `completeAgentTask`, `failAgentTask`, and
`tools/AgentTool/agentToolUtils.ts` -> `runAsyncAgentLifecycle`, `extractPartialResult`.

A background agent is a worker the Agent tool did not wait for. Its task record lives in
`AppState.tasks` under the agent's own id (upstream registers the task with the `agentId`), is
written only through the root setter, and ends in exactly one `<task-notification>` enqueued at
`later` priority on the run's command queue — the runner delivers it as a turn of its own.

**TDD.** Only agents without a `phase` may run here (`agents/tool.py` refuses the rest): an
async worker's app-state writes are discarded upstream (`shareSetAppState: !isAsync`), so a
background tester's `RunTests` could never confirm RED.

Not ported: progress tracking and summaries (UI), the panel/retain/eviction fields, the
transcript-viewer message mirror, and `classifyHandoffIfNeeded`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.messages import AIMessage

from app.loop.command_queue import CommandQueue, QueuedCommand
from app.loop.context import AppState, CancelToken
from app.loop.messages import Message
from app.loop.tasks.framework import SetAppState, register_task, update_task_state
from app.loop.tasks.task import TaskStateBase

logger = logging.getLogger(__name__)

#: `constants/xml.ts`.
TASK_NOTIFICATION_TAG = "task-notification"
TASK_ID_TAG = "task-id"
TOOL_USE_ID_TAG = "tool-use-id"
OUTPUT_FILE_TAG = "output-file"
STATUS_TAG = "status"
SUMMARY_TAG = "summary"


@dataclass(frozen=True)
class LocalAgentTaskState(TaskStateBase):
    """`LocalAgentTaskState`, the fields a headless run uses."""

    agent_id: str = ""
    prompt: str = ""
    agent_type: str = ""
    cancel: CancelToken = field(default_factory=CancelToken)
    error: str | None = None
    #: The worker's final text (upstream keeps the whole `AgentToolResult`).
    result: str | None = None
    retrieved: bool = False
    is_backgrounded: bool = True
    #: Messages queued mid-turn by `SendMessage`, drained at the worker's tool-round boundaries.
    pending_messages: tuple[str, ...] = ()


def register_async_agent(
    *,
    agent_id: str,
    description: str,
    prompt: str,
    agent_type: str,
    set_app_state: SetAppState,
    output_file: str,
    tool_use_id: str | None = None,
    now: float | None = None,
) -> LocalAgentTaskState:
    """
    `registerAsyncAgent`: a running, backgrounded task with its own cancel token — not linked to
    the parent's, so a background agent survives the main thread being cancelled.
    """
    task = LocalAgentTaskState(
        id=agent_id,
        type="local_agent",
        status="running",
        description=description,
        tool_use_id=tool_use_id,
        start_time=time.time() if now is None else now,
        output_file=output_file,
        agent_id=agent_id,
        prompt=prompt,
        agent_type=agent_type,
    )
    register_task(task, set_app_state)
    return task


def queue_pending_message(task_id: str, message: str, set_app_state: SetAppState) -> None:
    """`queuePendingMessage`."""
    update_task_state(task_id, set_app_state, lambda t: replace(t, pending_messages=(*t.pending_messages, message)))


def drain_pending_messages(
    task_id: str, get_app_state: Callable[[], AppState], set_app_state: SetAppState
) -> list[str]:
    """`drainPendingMessages`: the queued messages, removed."""
    task = get_app_state().tasks.get(task_id)
    if not isinstance(task, LocalAgentTaskState) or not task.pending_messages:
        return []
    update_task_state(task_id, set_app_state, lambda t: replace(t, pending_messages=()))
    return list(task.pending_messages)


def _finish(task_id: str, set_app_state: SetAppState, now: float | None, **changes: Any) -> None:
    end = time.time() if now is None else now
    update_task_state(
        task_id, set_app_state,
        lambda t: t if t.status != "running" else replace(t, end_time=end, **changes),
    )


def kill_async_agent(task_id: str, set_app_state: SetAppState, now: float | None = None) -> None:
    """`killAsyncAgent`: abort and mark killed; a no-op for a task that is not running."""

    def kill(state: AppState) -> AppState:
        task = state.tasks.get(task_id)
        if task is None or task.status != "running":
            return state
        task.cancel.cancel()
        return state

    set_app_state(kill)
    _finish(task_id, set_app_state, now, status="killed")


def complete_agent_task(task_id: str, result: str, set_app_state: SetAppState, now: float | None = None) -> None:
    """`completeAgentTask`."""
    _finish(task_id, set_app_state, now, status="completed", result=result)


def fail_agent_task(task_id: str, error: str, set_app_state: SetAppState, now: float | None = None) -> None:
    """`failAgentTask`."""
    _finish(task_id, set_app_state, now, status="failed", error=error)


def enqueue_agent_notification(
    *,
    queue: CommandQueue | None,
    task_id: str,
    description: str,
    status: str,
    set_app_state: SetAppState,
    output_file: str,
    error: str | None = None,
    final_message: str | None = None,
    usage: tuple[int, int, int] | None = None,
    tool_use_id: str | None = None,
) -> None:
    """
    `enqueueAgentNotification`: claim the task's `notified` flag, then enqueue upstream's XML.
    A task already notified (or unknown) enqueues nothing. `usage` is
    `(total_tokens, tool_uses, duration_ms)`.
    """
    claimed = {"ok": False}

    def claim(task: Any) -> Any:
        if task.notified:
            return task
        claimed["ok"] = True
        return replace(task, notified=True)

    update_task_state(task_id, set_app_state, claim)
    if not claimed["ok"] or queue is None:
        return
    if status == "completed":
        summary = f'Agent "{description}" completed'
    elif status == "failed":
        summary = f'Agent "{description}" failed: {error or "Unknown error"}'
    else:
        summary = f'Agent "{description}" was stopped'
    tool_use_line = f"\n<{TOOL_USE_ID_TAG}>{tool_use_id}</{TOOL_USE_ID_TAG}>" if tool_use_id else ""
    result_section = f"\n<result>{final_message}</result>" if final_message else ""
    usage_section = (
        f"\n<usage><total_tokens>{usage[0]}</total_tokens><tool_uses>{usage[1]}</tool_uses>"
        f"<duration_ms>{usage[2]}</duration_ms></usage>"
        if usage else ""
    )
    message = (
        f"<{TASK_NOTIFICATION_TAG}>\n"
        f"<{TASK_ID_TAG}>{task_id}</{TASK_ID_TAG}>{tool_use_line}\n"
        f"<{OUTPUT_FILE_TAG}>{output_file}</{OUTPUT_FILE_TAG}>\n"
        f"<{STATUS_TAG}>{status}</{STATUS_TAG}>\n"
        f"<{SUMMARY_TAG}>{summary}</{SUMMARY_TAG}>{result_section}{usage_section}\n"
        f"</{TASK_NOTIFICATION_TAG}>"
    )
    queue.enqueue_pending_notification(QueuedCommand(value=message, mode="task-notification"))


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def extract_partial_result(messages: Sequence[Message]) -> str | None:
    """`extractPartialResult`: the latest assistant message with text."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _text_of(message)
            if text:
                return text
    return None


async def run_async_agent_lifecycle(
    *,
    task_id: str,
    cancel: CancelToken,
    stream: AsyncIterator[Message],
    description: str,
    agent_type: str,
    start_time: float,
    root_set_app_state: SetAppState,
    queue: CommandQueue | None,
    output_file: str,
    tool_use_id: str | None = None,
    now: Callable[[], float] = time.time,
) -> None:
    """
    `runAsyncAgentLifecycle`: drive the worker, then complete, kill or fail its task and enqueue
    its one notification. A cancelled worker is reported `killed` with its partial result (our
    loop ends on cancellation instead of raising upstream's `AbortError`).
    """
    from app.loop.agents.tool import finalize_agent_tool

    messages: list[Message] = []
    notify = functools.partial(
        enqueue_agent_notification, queue=queue, task_id=task_id, description=description,
        set_app_state=root_set_app_state, output_file=output_file, tool_use_id=tool_use_id,
    )
    try:
        async for message in stream:
            messages.append(message)
        if cancel.cancelled:
            kill_async_agent(task_id, root_set_app_state, now())
            notify(status="killed", final_message=extract_partial_result(messages))
            return
        result = finalize_agent_tool(messages, task_id, agent_type, start_time, now=now())
        final_message = "\n".join(result.content)
        complete_agent_task(task_id, final_message, root_set_app_state, now())
        notify(
            status="completed", final_message=final_message,
            usage=(result.total_tokens, result.total_tool_use_count, result.total_duration_ms),
        )
    except Exception as exc:  # noqa: BLE001 - upstream's catch: any failure fails the task
        logger.warning("Background agent %s failed: %s", task_id, exc)
        fail_agent_task(task_id, str(exc), root_set_app_state, now())
        notify(status="failed", error=str(exc))


#: Running background lifecycles, held so the event loop does not drop them mid-run (upstream's
#: `void runAsyncAgentLifecycle(...)` is kept alive by the JS runtime itself).
_BACKGROUND_RUNS: set[asyncio.Task[None]] = set()


def start_background_lifecycle(lifecycle: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Start a lifecycle without awaiting it, as upstream's `void` call does."""
    run = asyncio.ensure_future(lifecycle)
    _BACKGROUND_RUNS.add(run)
    run.add_done_callback(_BACKGROUND_RUNS.discard)
    return run


__all__ = [
    "LocalAgentTaskState",
    "complete_agent_task",
    "drain_pending_messages",
    "enqueue_agent_notification",
    "extract_partial_result",
    "fail_agent_task",
    "kill_async_agent",
    "queue_pending_message",
    "register_async_agent",
    "run_async_agent_lifecycle",
    "start_background_lifecycle",
]
