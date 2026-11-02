"""
The SDK-only system events a headless run streams: task lifecycle and session state.

Ported from:
- `reference/claude-code/src/utils/sdkEventQueue.ts` -> `MAX_QUEUE_SIZE`, `enqueueSdkEvent` (only in a
  non-interactive session), `drainSdkEvents` (each event stamped with a uuid and the session id),
  `emitTaskTerminatedSdk`,
- `reference/claude-code/src/utils/task/sdkProgress.ts` -> `emitTaskProgress`,
- `reference/claude-code/src/utils/sessionState.ts` -> `notifySessionStateChanged`'s event, behind
  `TDDAGENTS_EMIT_SESSION_STATE_EVENTS` (upstream's `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS`).

Upstream's queue is the process's; here it is the run's (`AppState.sdk_events`), since one process
may hold several sessions, and the gate reads the run's `bootstrap.is_interactive`.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from typing import Any

#: `MAX_QUEUE_SIZE`: the oldest events are dropped beyond it.
MAX_QUEUE_SIZE = 1000
#: `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS`.
EMIT_SESSION_STATE_EVENTS_ENV = "TDDAGENTS_EMIT_SESSION_STATE_EVENTS"


class SdkEventQueue:
    """The run's queue (`queue`)."""

    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_QUEUE_SIZE)

    def drain(self, session_id: str) -> list[dict[str, Any]]:
        """`drainSdkEvents`: every queued event, stamped, the queue emptied."""
        drained = [{**event, "uuid": str(uuid.uuid4()), "session_id": session_id} for event in self.events]
        self.events.clear()
        return drained


def enqueue_sdk_event(state: Any, event: dict[str, Any]) -> None:
    """`enqueueSdkEvent`: only a non-interactive session queues SDK events."""
    if state is None or getattr(getattr(state, "bootstrap", None), "is_interactive", True):
        return
    state.sdk_events.events.append(event)


def emit_task_started(state: Any, task: Any) -> None:
    """`registerTask`'s `task_started` event."""
    event: dict[str, Any] = {"type": "system", "subtype": "task_started", "task_id": task.id,
                             "description": getattr(task, "description", ""), "task_type": getattr(task, "type", None)}
    for key, attribute in (("tool_use_id", "tool_use_id"), ("prompt", "prompt")):
        value = getattr(task, attribute, None)
        if value is not None:
            event[key] = value
    enqueue_sdk_event(state, event)


def emit_task_progress(
    state: Any, *, task_id: str, tool_use_id: str | None, description: str, start_time: float, total_tokens: int,
    tool_uses: int, last_tool_name: str | None = None, summary: str | None = None, now: float | None = None,
) -> None:
    """`emitTaskProgress`: a `task_progress` event with the task's usage so far."""
    event: dict[str, Any] = {
        "type": "system", "subtype": "task_progress", "task_id": task_id, "description": description,
        "usage": {"total_tokens": total_tokens, "tool_uses": tool_uses,
                  "duration_ms": int(((now if now is not None else time.time()) - start_time) * 1000)},
    }
    for key, value in (("tool_use_id", tool_use_id), ("last_tool_name", last_tool_name), ("summary", summary)):
        if value is not None:
            event[key] = value
    enqueue_sdk_event(state, event)


def emit_task_terminated_sdk(state: Any, task_id: str, status: str, *, tool_use_id: str | None = None,
                             summary: str = "", output_file: str = "", usage: dict[str, int] | None = None) -> None:
    """`emitTaskTerminatedSdk`: a `task_notification` event for a task that ended without its XML."""
    event: dict[str, Any] = {"type": "system", "subtype": "task_notification", "task_id": task_id, "status": status,
                             "output_file": output_file, "summary": summary}
    if tool_use_id is not None:
        event["tool_use_id"] = tool_use_id
    if usage is not None:
        event["usage"] = usage
    enqueue_sdk_event(state, event)


def notify_session_state_changed(state: Any, session_state: str) -> None:
    """`notifySessionStateChanged`'s SDK event: `idle`, `running` or `requires_action`, when opted in."""
    from app.utils.claudemd import is_env_truthy

    if is_env_truthy(os.environ.get(EMIT_SESSION_STATE_EVENTS_ENV)):
        enqueue_sdk_event(state, {"type": "system", "subtype": "session_state_changed", "state": session_state})


__all__ = [
    "EMIT_SESSION_STATE_EVENTS_ENV",
    "MAX_QUEUE_SIZE",
    "SdkEventQueue",
    "emit_task_progress",
    "emit_task_started",
    "emit_task_terminated_sdk",
    "enqueue_sdk_event",
    "notify_session_state_changed",
]
