"""
The hook event vocabulary and each event's match query.

Ported from:
- `reference/claude-code/src/utils/hooks/hookEvents.ts` -> `ALWAYS_EMITTED_HOOK_EVENTS`,
  `MAX_PENDING_EVENTS`, the started / response events and their emitter
  (`registerHookEventHandler`, `emitHookStarted`, `emitHookResponse`, `setAllHookEventsEnabled`),
  here per run (`HookEventEmitter`, on the run's hook dispatcher) rather than per process. The
  periodic `progress` event (`startHookProgressInterval`) is not ported: a command hook's output is
  read when it exits.
- `reference/claude-code/src/entrypoints/sdk/coreTypes.ts` -> `HOOK_EVENTS` (27 events)
- `reference/claude-code/src/utils/hooks.ts` -> `getMatchingHooks` (the `matchQuery` switch),
  `TOOL_HOOK_EXECUTION_TIMEOUT_MS`, `SESSION_END_HOOK_TIMEOUT_MS_DEFAULT`

All 27 events are declared so a settings file naming any of them parses. Which of them this
loop actually fires is decided at the call sites, as upstream decides it at its own.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class HookEvent(StrEnum):
    """`HOOK_EVENTS`, in upstream's declaration order."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"


HOOK_EVENTS: tuple[str, ...] = tuple(event.value for event in HookEvent)

#: `TOOL_HOOK_EXECUTION_TIMEOUT_MS`: a hook with no `timeout` of its own gets ten minutes.
TOOL_HOOK_EXECUTION_TIMEOUT_MS = 10 * 60 * 1000

#: `SESSION_END_HOOK_TIMEOUT_MS_DEFAULT`: SessionEnd runs during shutdown and gets 1.5 s.
SESSION_END_HOOK_TIMEOUT_MS_DEFAULT = 1500

#: The events whose `if` conditions can be evaluated (`prepareIfConditionMatcher`): only
#: these carry a tool call to match a permission-rule pattern against.
TOOL_EVENTS: frozenset[str] = frozenset(
    {
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.POST_TOOL_USE_FAILURE,
        HookEvent.PERMISSION_REQUEST,
    }
)

_MATCH_FIELD: dict[str, str] = {
    HookEvent.PRE_TOOL_USE: "tool_name",
    HookEvent.POST_TOOL_USE: "tool_name",
    HookEvent.POST_TOOL_USE_FAILURE: "tool_name",
    HookEvent.PERMISSION_REQUEST: "tool_name",
    HookEvent.PERMISSION_DENIED: "tool_name",
    HookEvent.SESSION_START: "source",
    HookEvent.SETUP: "trigger",
    HookEvent.PRE_COMPACT: "trigger",
    HookEvent.POST_COMPACT: "trigger",
    HookEvent.NOTIFICATION: "notification_type",
    HookEvent.SESSION_END: "reason",
    HookEvent.STOP_FAILURE: "error",
    HookEvent.SUBAGENT_START: "agent_type",
    HookEvent.SUBAGENT_STOP: "agent_type",
    HookEvent.ELICITATION: "mcp_server_name",
    HookEvent.ELICITATION_RESULT: "mcp_server_name",
    HookEvent.CONFIG_CHANGE: "source",
    HookEvent.INSTRUCTIONS_LOADED: "load_reason",
}


def is_hook_event(value: str) -> bool:
    """`isHookEvent`."""
    return value in HOOK_EVENTS


def match_query_for(hook_input: Mapping[str, Any]) -> str | None:
    """
    The value an event's matchers are tested against (`getMatchingHooks`' switch).

    `None` means the event has no match query (Stop, UserPromptSubmit, the teammate
    events), and then every matcher applies regardless of its pattern. `FileChanged`
    matches on the file's basename.
    """
    event = str(hook_input.get("hook_event_name", ""))
    if event == HookEvent.FILE_CHANGED:
        path = hook_input.get("file_path")
        return posixpath.basename(str(path)) if path is not None else None
    field_name = _MATCH_FIELD.get(event)
    if field_name is None:
        return None
    value = hook_input.get(field_name)
    return None if value is None else str(value)


#: `ALWAYS_EMITTED_HOOK_EVENTS`: emitted even without `--include-hook-events`.
ALWAYS_EMITTED_HOOK_EVENTS = ("SessionStart", "Setup")
#: `MAX_PENDING_EVENTS`: what is kept for a handler not registered yet.
MAX_PENDING_EVENTS = 100


class HookEventEmitter:
    """`hookEvents.ts`' module state, for one run: the handler, the pending events and the switch."""

    def __init__(self) -> None:
        self.handler: Any = None
        self.pending: list[dict[str, Any]] = []
        self.all_hook_events_enabled = False

    def register_handler(self, handler: Any) -> None:
        """`registerHookEventHandler`: the events waiting so far go to it first."""
        self.handler = handler
        if handler is not None and self.pending:
            pending, self.pending = self.pending, []
            for event in pending:
                handler(event)

    def should_emit(self, hook_event: str) -> bool:
        """`shouldEmit`."""
        return hook_event in ALWAYS_EMITTED_HOOK_EVENTS or (self.all_hook_events_enabled and is_hook_event(hook_event))

    def _emit(self, event: dict[str, Any]) -> None:
        if self.handler is not None:
            self.handler(event)
            return
        self.pending.append(event)
        if len(self.pending) > MAX_PENDING_EVENTS:
            self.pending.pop(0)

    def emit_started(self, hook_id: str, hook_name: str, hook_event: str) -> None:
        """`emitHookStarted`."""
        if self.should_emit(hook_event):
            self._emit({"type": "started", "hookId": hook_id, "hookName": hook_name, "hookEvent": hook_event})

    def emit_response(self, *, hook_id: str, hook_name: str, hook_event: str, output: str, stdout: str,
                      stderr: str, exit_code: int | None, outcome: str) -> None:
        """`emitHookResponse`: `outcome` is `success`, `error` or `cancelled`."""
        if self.should_emit(hook_event):
            self._emit({"type": "response", "hookId": hook_id, "hookName": hook_name, "hookEvent": hook_event,
                        "output": output, "stdout": stdout, "stderr": stderr, "exitCode": exit_code,
                        "outcome": outcome})


__all__ = [
    "ALWAYS_EMITTED_HOOK_EVENTS",
    "MAX_PENDING_EVENTS",
    "HookEventEmitter",
    "HOOK_EVENTS",
    "SESSION_END_HOOK_TIMEOUT_MS_DEFAULT",
    "TOOL_EVENTS",
    "TOOL_HOOK_EXECUTION_TIMEOUT_MS",
    "HookEvent",
    "is_hook_event",
    "match_query_for",
]
