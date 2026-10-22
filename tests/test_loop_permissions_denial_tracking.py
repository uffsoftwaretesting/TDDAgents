"""Phase 4b: auto-mode denial tracking and the classifier allowlist (offline)."""

from __future__ import annotations

import asyncio

from app.loop.permissions.classifier_decision import SAFE_YOLO_ALLOWLISTED_TOOLS, is_auto_mode_allowlisted_tool
from app.loop.permissions.denial_tracking import (
    MAX_CONSECUTIVE_DENIALS,
    MAX_TOTAL_DENIALS,
    DenialTrackingState,
    create_denial_tracking_state,
    record_denial,
    record_success,
    should_fallback_to_prompting,
)
from app.loop.permissions.yolo_classifier_names import YOLO_CLASSIFIER_TOOL_NAME
from app.loop.context import AppStateStore, tool_context_for
from app.loop.hooks.execute import execute_permission_denied_hooks
from app.loop.hooks.output import AggregatedHookResult
from app.loop.hooks.tool_hooks import PERMISSION_DENIED_RETRY_MESSAGE, run_permission_denied_hooks
from tests.hook_doubles import ScriptedDispatcher


def test_upstream_limits():
    assert (MAX_CONSECUTIVE_DENIALS, MAX_TOTAL_DENIALS) == (3, 20)
    assert create_denial_tracking_state() == DenialTrackingState(0, 0)


def test_record_denial_counts_both():
    assert record_denial(DenialTrackingState(1, 5)) == DenialTrackingState(2, 6)


def test_record_success_resets_only_the_streak_and_keeps_identity_when_nothing_changes():
    state = DenialTrackingState(2, 7)
    assert record_success(state) == DenialTrackingState(0, 7)
    clean = DenialTrackingState(0, 7)
    assert record_success(clean) is clean


def test_fallback_thresholds_are_inclusive():
    assert should_fallback_to_prompting(DenialTrackingState(2, 19)) is False
    assert should_fallback_to_prompting(DenialTrackingState(3, 3)) is True
    assert should_fallback_to_prompting(DenialTrackingState(0, 20)) is True


def test_allowlist_names():
    for name in ("ReadFile", "Grep", "Glob", "TodoWrite", "TaskStop", "TaskOutput", YOLO_CLASSIFIER_TOOL_NAME):
        assert is_auto_mode_allowlisted_tool(name)
    for name in ("Bash", "WriteFile", "Edit", "RunTests", "Agent", "Read"):
        assert not is_auto_mode_allowlisted_tool(name)
    assert YOLO_CLASSIFIER_TOOL_NAME == "classify_result"
    assert len(SAFE_YOLO_ALLOWLISTED_TOOLS) == 22


def test_permission_denied_hook_input_and_retry():
    dispatcher = ScriptedDispatcher(script={"PermissionDenied": [AggregatedHookResult(retry=True)]})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher, agent_id="a1", agent_type="tester")
    assert asyncio.run(run_permission_denied_hooks(ctx, "Bash", {"command": "rm x"}, "tu1", "too risky")) is True
    assert dispatcher.payload("PermissionDenied") == {
        "session_id": "s", "transcript_path": "", "cwd": ".", "permission_mode": "default",
        "agent_id": "a1", "agent_type": "tester", "hook_event_name": "PermissionDenied",
        "tool_name": "Bash", "tool_input": {"command": "rm x"}, "tool_use_id": "tu1", "reason": "too risky",
    }
    assert PERMISSION_DENIED_RETRY_MESSAGE == (
        "The PermissionDenied hook indicated this command is now approved. You may retry it if you would like."
    )


def test_permission_denied_without_retry_or_dispatcher():
    quiet = ScriptedDispatcher(script={"PermissionDenied": [AggregatedHookResult()]})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=quiet)
    assert asyncio.run(run_permission_denied_hooks(ctx, "Bash", {}, "t", "r")) is False

    async def collect() -> list[AggregatedHookResult]:
        bare = tool_context_for(AppStateStore())
        return [r async for r in execute_permission_denied_hooks(bare, "B", "t", {}, "r", None)]

    assert asyncio.run(collect()) == []
