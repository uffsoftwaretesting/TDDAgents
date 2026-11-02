"""
Background agents (Phase 8).

Ported from `reference/claude-code/src/tasks/LocalAgentTask/LocalAgentTask.tsx` ->
`LocalAgentTaskState`, `registerAsyncAgent`, `queuePendingMessage`, `drainPendingMessages`,
`enqueueAgentNotification`, `killAsyncAgent`, `completeAgentTask`, `failAgentTask`, and
`ToolActivity`, `AgentProgress`, `MAX_RECENT_ACTIVITIES`, `ProgressTracker`, `createProgressTracker`,
`getTokenCountFromTracker`, `updateProgressFromMessage`, `getProgressUpdate`,
`createActivityDescriptionResolver`, `updateAgentProgress`, and
`tools/AgentTool/agentToolUtils.ts` -> `runAsyncAgentLifecycle` (progress updated per message),
`extractPartialResult`.

A background agent is a worker the Agent tool did not wait for. Its task record lives in
`AppState.tasks` under the agent's own id (upstream registers the task with the `agentId`), is
written only through the root setter, and ends in exactly one `<task-notification>` enqueued at
`later` priority on the run's command queue — the runner delivers it as a turn of its own.

**TDD.** Only agents without a `phase` may run here (`tools/agent_tool/agent_tool.py` refuses the rest): an
async worker's app-state writes are discarded upstream (`shareSetAppState: !isAsync`), so a
background tester's `RunTests` could never end RED.

Not ported: the periodic background summaries (`updateAgentSummary`), the SDK `task_progress`
event, the panel/retain/eviction fields, the transcript-viewer message mirror, and
`classifyHandoffIfNeeded`.
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

from app.state.app_state_store import AppState
from app.task import TaskStateBase
from app.tool import CancelToken
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.utils.messages import Message
from app.utils.task.framework import SetAppState, register_task, update_task_state

logger = logging.getLogger(__name__)

#: `constants/xml.ts`.
TASK_NOTIFICATION_TAG = "task-notification"
TASK_ID_TAG = "task-id"
TOOL_USE_ID_TAG = "tool-use-id"
OUTPUT_FILE_TAG = "output-file"
STATUS_TAG = "status"
SUMMARY_TAG = "summary"


@dataclass(frozen=True, slots=True)
class ToolActivity:
    """`ToolActivity`: one tool use, classified when it was recorded."""

    tool_name: str
    input: dict[str, Any]
    #: `getActivityDescription` of the tool ("Reading src/foo.ts").
    activity_description: str | None = None
    is_search: bool | None = None
    is_read: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """The camel-cased shape `summarize_recent_activities` reads."""
        return {"toolName": self.tool_name, "input": self.input, "activityDescription": self.activity_description,
                "isSearch": self.is_search, "isRead": self.is_read}


@dataclass(frozen=True, slots=True)
class AgentProgress:
    """`AgentProgress`."""

    tool_use_count: int
    token_count: int
    last_activity: ToolActivity | None = None
    recent_activities: tuple[ToolActivity, ...] = ()
    summary: str | None = None


#: `MAX_RECENT_ACTIVITIES`.
MAX_RECENT_ACTIVITIES = 5


@dataclass
class ProgressTracker:
    """
    `ProgressTracker`: input tokens are cumulative per turn in the API, so the latest is kept;
    output tokens are per turn, so they are summed.
    """

    tool_use_count: int = 0
    latest_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    recent_activities: list[ToolActivity] = field(default_factory=list)


def create_progress_tracker() -> ProgressTracker:
    """`createProgressTracker`."""
    return ProgressTracker()


def get_token_count_from_tracker(tracker: ProgressTracker) -> int:
    """`getTokenCountFromTracker`."""
    return tracker.latest_input_tokens + tracker.cumulative_output_tokens


ActivityDescriptionResolver = Callable[[str, dict[str, Any]], str | None]


def create_activity_description_resolver(tools: Sequence[Any], context: Any = None) -> ActivityDescriptionResolver:
    """`createActivityDescriptionResolver`: the tool's `getActivityDescription`, by name."""
    from app.tool import find_tool_by_name

    def resolve(tool_name: str, tool_input: dict[str, Any]) -> str | None:
        tool = find_tool_by_name(tools, tool_name)
        return tool.get_activity_description(tool_input, context) if tool is not None else None

    return resolve


def update_progress_from_message(
    tracker: ProgressTracker, message: Any, resolve_activity_description: ActivityDescriptionResolver | None = None,
    tools: Sequence[Any] | None = None, context: Any = None,
) -> None:
    """`updateProgressFromMessage`: an assistant message's usage and its tool uses."""
    if not isinstance(message, AIMessage):
        return
    usage: dict[str, Any] = dict(message.usage_metadata or {})
    details: dict[str, Any] = dict(usage.get("input_token_details") or {})
    tracker.latest_input_tokens = (int(usage.get("input_tokens", 0)) + int(details.get("cache_creation", 0) or 0)
                                   + int(details.get("cache_read", 0) or 0))
    tracker.cumulative_output_tokens += int(usage.get("output_tokens", 0))
    for call in message.tool_calls:
        tracker.tool_use_count += 1
        name, tool_input = str(call.get("name")), dict(call.get("args") or {})
        classification = None
        if tools is not None:
            from app.utils.collapse_read_search import get_tool_search_or_read_info

            classification = get_tool_search_or_read_info(name, tool_input, tools, context)
        tracker.recent_activities.append(ToolActivity(
            tool_name=name, input=tool_input,
            activity_description=(resolve_activity_description(name, tool_input)
                                  if resolve_activity_description else None),
            is_search=classification.is_search if classification else None,
            is_read=classification.is_read if classification else None,
        ))
    del tracker.recent_activities[:-MAX_RECENT_ACTIVITIES]


def get_progress_update(tracker: ProgressTracker) -> AgentProgress:
    """`getProgressUpdate`."""
    activities = tuple(tracker.recent_activities)
    return AgentProgress(tool_use_count=tracker.tool_use_count, token_count=get_token_count_from_tracker(tracker),
                         last_activity=activities[-1] if activities else None, recent_activities=activities)


def update_agent_progress(task_id: str, progress: AgentProgress, set_app_state: SetAppState) -> None:
    """`updateAgentProgress`: a running task's progress, keeping a summary it already has."""

    def update(task: LocalAgentTaskState) -> LocalAgentTaskState:
        if task.status != "running":
            return task
        summary = task.progress.summary if task.progress is not None else None
        return replace(task, progress=replace(progress, summary=summary) if summary else progress)

    update_task_state(task_id, set_app_state, update)


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
    #: `progress`: tool uses, tokens and the recent activities, while it runs.
    progress: AgentProgress | None = None


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
    tools: Sequence[Any] = (),
    context: Any = None,
) -> None:
    """
    `runAsyncAgentLifecycle`: drive the worker, then complete, kill or fail its task and enqueue
    its one notification. A cancelled worker is reported `killed` with its partial result (our
    loop ends on cancellation instead of raising upstream's `AbortError`).
    """
    from app.tools.agent_tool.agent_tool import finalize_agent_tool

    messages: list[Message] = []
    tracker = create_progress_tracker()
    resolve_activity = create_activity_description_resolver(tools, context)
    notify = functools.partial(
        enqueue_agent_notification, queue=queue, task_id=task_id, description=description,
        set_app_state=root_set_app_state, output_file=output_file, tool_use_id=tool_use_id,
    )
    try:
        async for message in stream:
            messages.append(message)
            update_progress_from_message(tracker, message, resolve_activity, tools, context)
            progress = get_progress_update(tracker)
            update_agent_progress(task_id, progress, root_set_app_state)
            last_tool = _last_tool_use_name(message)
            if last_tool is not None and context is not None:
                # `runAsyncAgentLifecycle` -> `emitTaskProgress`, after each message that calls a tool.
                from app.utils.sdk_event_queue import emit_task_progress

                activity = progress.last_activity.activity_description if progress.last_activity else None
                emit_task_progress(
                    context.get_app_state(), task_id=task_id, tool_use_id=tool_use_id,
                    description=activity or description, start_time=start_time, total_tokens=progress.token_count,
                    tool_uses=progress.tool_use_count, last_tool_name=last_tool, now=now())
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


def _last_tool_use_name(message: Any) -> str | None:
    """`getLastToolUseName`: the last tool an assistant message calls."""
    calls = getattr(message, "tool_calls", None) if isinstance(message, AIMessage) else None
    return str(calls[-1].get("name")) if calls else None


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
    "MAX_RECENT_ACTIVITIES",
    "AgentProgress",
    "LocalAgentTaskState",
    "ProgressTracker",
    "ToolActivity",
    "complete_agent_task",
    "create_activity_description_resolver",
    "create_progress_tracker",
    "drain_pending_messages",
    "enqueue_agent_notification",
    "extract_partial_result",
    "fail_agent_task",
    "get_progress_update",
    "get_token_count_from_tracker",
    "kill_async_agent",
    "queue_pending_message",
    "register_async_agent",
    "run_async_agent_lifecycle",
    "start_background_lifecycle",
    "update_agent_progress",
    "update_progress_from_message",
]
