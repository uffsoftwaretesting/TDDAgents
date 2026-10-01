"""
Hook event vocabulary and match key extraction (Part H1).

Ported from:
- `reference/claude-code/src/entrypoints/sdk/coreTypes.ts` -> `HOOK_EVENTS`
- `reference/claude-code/src/utils/hooks.ts` -> `matchesPattern`, `getMatchingHooks`
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import Any


class HookEvent(StrEnum):
    """
    Standard hook events across Claude Code and TDDAgents lifecycle.
    """

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
    CONFIG_CHANGE = "ConfigChange"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    FILE_CHANGED = "FileChanged"
    CWD_CHANGED = "CwdChanged"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"


HOOK_EVENTS: tuple[str, ...] = tuple(e.value for e in HookEvent)


def extract_match_key(event: HookEvent | str, payload: dict[str, Any]) -> str | None:
    """
    Extracts the match key for `event` from the hook payload.

    Ported from `reference/claude-code/src/utils/hooks.ts:1615-1670`.
    """
    event_str = str(event)

    if event_str in {
        HookEvent.PRE_TOOL_USE.value,
        HookEvent.POST_TOOL_USE.value,
        HookEvent.POST_TOOL_USE_FAILURE.value,
        HookEvent.PERMISSION_REQUEST.value,
        HookEvent.PERMISSION_DENIED.value,
    }:
        tool_name = payload.get("tool_name")
        return str(tool_name) if tool_name is not None else None

    if event_str in {HookEvent.SESSION_START.value, HookEvent.CONFIG_CHANGE.value}:
        source = payload.get("source")
        return str(source) if source is not None else None

    if event_str in {
        HookEvent.SETUP.value,
        HookEvent.PRE_COMPACT.value,
        HookEvent.POST_COMPACT.value,
    }:
        trigger = payload.get("trigger")
        return str(trigger) if trigger is not None else None

    if event_str == HookEvent.NOTIFICATION.value:
        notification_type = payload.get("notification_type")
        return str(notification_type) if notification_type is not None else None

    if event_str == HookEvent.SESSION_END.value:
        reason = payload.get("reason")
        return str(reason) if reason is not None else None

    if event_str == HookEvent.STOP_FAILURE.value:
        error = payload.get("error")
        return str(error) if error is not None else None

    if event_str in {HookEvent.SUBAGENT_START.value, HookEvent.SUBAGENT_STOP.value}:
        agent_type = payload.get("agent_type")
        return str(agent_type) if agent_type is not None else None

    if event_str in {HookEvent.ELICITATION.value, HookEvent.ELICITATION_RESULT.value}:
        mcp = payload.get("mcp_server_name")
        return str(mcp) if mcp is not None else None

    if event_str == HookEvent.INSTRUCTIONS_LOADED.value:
        load_reason = payload.get("load_reason")
        return str(load_reason) if load_reason is not None else None

    if event_str == HookEvent.FILE_CHANGED.value:
        path = payload.get("file_path")
        return os.path.basename(str(path)) if path else None

    # Events with no specific match key (match all matchers or wildcard)
    return None


def matches_pattern(match_query: str | None, matcher: str | None) -> bool:
    """
    Checks if a match query matches the pattern.

    Ported from `reference/claude-code/src/utils/hooks.ts:1346-1381`.

    - None or '*' matches everything.
    - If match_query is None, only matches None or '*'.
    - Pipe-separated list: 'Bash|Grep' -> exact match on either.
    - Simple alphanumeric/underscore -> exact match.
    - Otherwise -> regex search.
    """
    if not matcher or matcher == "*":
        return True

    if match_query is None:
        return False

    # Handle pipe-separated exact matches (e.g. 'Bash | RunCode' or 'Bash|Grep')
    if "|" in matcher:
        parts = {p.strip() for p in matcher.split("|") if p.strip()}
        if parts:
            return match_query in parts

    # Check if simple string (no regex special chars)
    if re.fullmatch(r"^[a-zA-Z0-9_]+$", matcher):
        return match_query == matcher

    # Otherwise regex
    try:
        return bool(re.search(matcher, match_query))
    except re.error:
        return False
