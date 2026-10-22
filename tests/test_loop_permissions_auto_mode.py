"""Phase 4b: the auto-mode branch of the outer gate (offline; the classifier is always a fake)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import AppState, AppStateStore, DenialTrackingBox, tool_context_for
from app.loop.hooks.output import AggregatedHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.auto_mode import (
    DENIAL_LIMIT_ABORT,
    AutoModeAbortError,
    SAFETY_CHECK_HEADLESS_REASON,
    TRANSCRIPT_TOO_LONG_ABORT,
    TRANSCRIPT_TOO_LONG_REASON,
)
from app.loop.permissions.denial_tracking import DenialTrackingState
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.types import PermissionBehavior, PermissionMode, PermissionResult, ToolPermissionContext
from app.loop.permissions.yolo_classifier import (
    YoloClassifierResult,
    build_classifier_unavailable_message,
    build_yolo_rejection_message,
)
from app.loop.tools.base import build_tool
from app.loop.tools.execution import run_tool_use
from app.loop.tools.types import ToolResult
from tests.hook_doubles import ScriptedDispatcher

ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK
AUTO = ToolPermissionContext(mode=PermissionMode.AUTO, should_avoid_permission_prompts=True)
AUTO_INTERACTIVE = ToolPermissionContext(mode=PermissionMode.AUTO)


class FakeClassifier:
    def __init__(self, *verdicts: YoloClassifierResult) -> None:
        self.verdicts = list(verdicts)
        self.calls: list[tuple[Any, ...]] = []

    async def __call__(self, messages, action, tools, perm_ctx, signal, **kwargs):
        self.calls.append((messages, action, tools, perm_ctx, signal, kwargs))
        return self.verdicts.pop(0)


def verdict(block: bool, reason: str = "r", **kw: Any) -> YoloClassifierResult:
    return YoloClassifierResult(should_block=block, reason=reason, model="m-1", **kw)


class AskTool:
    """Asks in every mode except acceptEdits, where `accept_edits` decides."""

    def __init__(self, name: str = "Bash", accept_edits: PermissionBehavior = ASK, reason: dict[str, Any] | None = None,
                 interaction: bool = False) -> None:
        self.name = name
        self.accept_edits = accept_edits
        self.reason = reason
        self.interaction = interaction
        self.modes: list[Any] = []

    def requires_user_interaction(self, args=None):
        return self.interaction

    def check_permissions(self, input_args, context):
        mode = context.get_app_state().tool_permission_context.mode
        self.modes.append(mode)
        if mode == PermissionMode.ACCEPT_EDITS and self.accept_edits == ALLOW:
            return PermissionResult(behavior=ALLOW, updated_input={"command": "rewritten"})
        return PermissionResult(behavior=ASK, message="ask?", decision_reason=self.reason)


def ctx_for(perm=AUTO, classifier=None, denial=None, ledger=None, local_denial_tracking=None, **kw):
    state = AppState(tool_permission_context=perm, denial_tracking=denial,
                     phase_ledger=ledger if ledger is not None else PhaseLedger())
    store = AppStateStore(state=state)
    ctx = tool_context_for(store, messages=(HumanMessage(content="task"),), run_id="r1", **kw)
    from dataclasses import replace

    return replace(ctx, auto_mode_classifier=classifier, local_denial_tracking=local_denial_tracking), store


def gate(tool, ctx, args=None):
    return asyncio.run(has_permissions_to_use_tool(tool, args if args is not None else {"command": "ls"}, ctx, "tu"))


def test_a_classifier_allow_is_an_allow_and_resets_the_streak():
    clf = FakeClassifier(verdict(False, "fine"))
    ctx, store = ctx_for(classifier=clf, denial=DenialTrackingState(2, 5))
    res = gate(AskTool(), ctx)
    assert (res.behavior, res.updated_input) == (ALLOW, {"command": "ls"})
    assert res.decision_reason == {"type": "classifier", "classifier": "auto-mode", "reason": "fine"}
    assert store.get().denial_tracking == DenialTrackingState(0, 5)
    messages, action, tools, perm_ctx, signal, kwargs = clf.calls[0]
    assert messages == (HumanMessage(content="task"),)
    assert perm_ctx.mode == PermissionMode.AUTO
    assert signal is ctx.cancel
    assert "tddagents_md" in kwargs


def test_a_block_denies_with_upstreams_message_and_records_the_denial():
    ctx, store = ctx_for(classifier=FakeClassifier(verdict(True, "deletes files")))
    res = gate(AskTool(), ctx)
    assert res.behavior == DENY
    assert res.message == build_yolo_rejection_message("deletes files")
    assert res.decision_reason == {"type": "classifier", "classifier": "auto-mode", "reason": "deletes files"}
    assert store.get().denial_tracking == DenialTrackingState(1, 1)
    assert ctx.cancel.cancelled is False


def test_an_unavailable_classifier_fails_closed():
    ctx, store = ctx_for(classifier=FakeClassifier(verdict(True, "x", unavailable=True)))
    res = gate(AskTool("Bash"), ctx)
    assert res.behavior == DENY
    assert res.message == build_classifier_unavailable_message("Bash", "m-1")
    assert res.decision_reason == {"type": "classifier", "classifier": "auto-mode", "reason": "Classifier unavailable"}
    assert store.get().denial_tracking is None


def test_a_transcript_too_long_aborts_headless_and_falls_back_interactively():
    ctx, _ = ctx_for(classifier=FakeClassifier(verdict(True, "x", transcript_too_long=True)))
    with pytest.raises(AutoModeAbortError, match="^" + TRANSCRIPT_TOO_LONG_ABORT + "$"):
        gate(AskTool(), ctx)
    assert ctx.cancel.cancelled is False
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=FakeClassifier(verdict(True, "x", transcript_too_long=True)))
    res = gate(AskTool(), ctx)
    assert res.behavior == ASK
    assert res.decision_reason == {"type": "other", "reason": TRANSCRIPT_TOO_LONG_REASON}
    assert ctx.cancel.cancelled is False


def test_the_third_consecutive_denial_aborts_a_headless_run():
    ctx, store = ctx_for(classifier=FakeClassifier(verdict(True, "bad")), denial=DenialTrackingState(2, 2))
    with pytest.raises(AutoModeAbortError, match="^" + DENIAL_LIMIT_ABORT + "$"):
        gate(AskTool(), ctx)
    assert ctx.cancel.cancelled is False
    assert store.get().denial_tracking == DenialTrackingState(3, 3)


def test_the_denial_limit_falls_back_to_asking_interactively():
    ctx, store = ctx_for(perm=AUTO_INTERACTIVE, classifier=FakeClassifier(verdict(True, "bad")),
                         denial=DenialTrackingState(2, 2))
    res = gate(AskTool(), ctx)
    assert res.behavior == ASK
    assert res.decision_reason == {
        "type": "classifier", "classifier": "auto-mode",
        "reason": "3 consecutive actions were blocked. Please review the transcript before continuing."
                  "\n\nLatest blocked action: bad",
    }
    assert store.get().denial_tracking == DenialTrackingState(3, 3)


def test_the_total_limit_resets_both_counters_interactively():
    ctx, store = ctx_for(perm=AUTO_INTERACTIVE, classifier=FakeClassifier(verdict(True, "bad")),
                         denial=DenialTrackingState(0, 19))
    res = gate(AskTool(), ctx)
    assert res.decision_reason["reason"].startswith("20 actions were blocked this session.")
    assert store.get().denial_tracking == DenialTrackingState(0, 0)


def test_the_accept_edits_fast_path_skips_the_classifier():
    clf = FakeClassifier()
    tool = AskTool(accept_edits=ALLOW)
    ctx, store = ctx_for(classifier=clf, denial=DenialTrackingState(1, 1))
    res = gate(tool, ctx)
    assert (res.behavior, res.updated_input) == (ALLOW, {"command": "rewritten"})
    assert res.decision_reason == {"type": "mode", "mode": "auto"}
    assert clf.calls == []
    assert tool.modes == [PermissionMode.AUTO, PermissionMode.ACCEPT_EDITS]
    assert store.get().denial_tracking == DenialTrackingState(0, 1)


def test_the_fast_path_also_overrides_an_explicit_permission_context():
    clf = FakeClassifier()
    tool = AskTool(accept_edits=ALLOW)
    ctx, _ = ctx_for(classifier=clf, permission_context=AUTO)
    assert gate(tool, ctx).behavior == ALLOW


def test_the_agent_tool_never_takes_the_fast_path():
    clf = FakeClassifier(verdict(False))
    tool = AskTool(name="Agent", accept_edits=ALLOW)
    ctx, _ = ctx_for(classifier=clf)
    assert gate(tool, ctx).decision_reason["type"] == "classifier"
    assert tool.modes == [PermissionMode.AUTO]


def test_an_allowlisted_tool_skips_the_classifier():
    clf = FakeClassifier()
    ctx, _ = ctx_for(classifier=clf)
    res = gate(AskTool(name="TodoWrite"), ctx, {"todos": []})
    assert (res.behavior, res.updated_input, res.decision_reason) == (
        ALLOW, {"todos": []}, {"type": "mode", "mode": "auto"})
    assert clf.calls == []


def test_a_safety_check_the_classifier_may_not_approve():
    reason = {"type": "safetyCheck", "reason": "win path", "classifierApprovable": False}
    clf = FakeClassifier()
    ctx, _ = ctx_for(classifier=clf)
    res = gate(AskTool(reason=reason), ctx)
    assert res.behavior == DENY
    assert res.decision_reason == {"type": "asyncAgent", "reason": SAFETY_CHECK_HEADLESS_REASON}
    assert res.message == "ask?"
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=clf)
    assert gate(AskTool(reason=reason), ctx).behavior == ASK
    assert clf.calls == []


def test_an_approvable_safety_check_goes_to_the_classifier():
    reason = {"type": "safetyCheck", "reason": "sensitive", "classifierApprovable": True}
    clf = FakeClassifier(verdict(False))
    ctx, _ = ctx_for(classifier=clf)
    assert gate(AskTool(reason=reason), ctx).behavior == ALLOW
    assert len(clf.calls) == 1


def test_a_tool_that_needs_a_person_keeps_its_ask():
    clf = FakeClassifier()
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=clf)
    assert gate(AskTool(interaction=True), ctx).behavior == ASK
    assert clf.calls == []


def test_a_rule_allow_in_auto_mode_resets_the_streak():
    tool = build_tool(name="Free", prompt="p", call=lambda a, c: ToolResult(content=""),
                      check_permissions=lambda a, c: PermissionResult(behavior=ALLOW))
    ctx, store = ctx_for(denial=DenialTrackingState(2, 4))
    assert gate(tool, ctx).behavior == ALLOW
    assert store.get().denial_tracking == DenialTrackingState(0, 4)
    ctx, store = ctx_for(perm=ToolPermissionContext(), denial=DenialTrackingState(2, 4))
    gate(tool, ctx)
    assert store.get().denial_tracking == DenialTrackingState(2, 4)


def test_a_worker_keeps_its_denials_in_its_local_box():
    box = DenialTrackingBox(DenialTrackingState(0, 3))
    ctx, store = ctx_for(classifier=FakeClassifier(verdict(True, "no")), local_denial_tracking=box)
    gate(AskTool(), ctx)
    assert box.state == DenialTrackingState(1, 4)
    assert store.get().denial_tracking is None


def test_red_denies_a_production_write_before_the_classifier_is_asked():
    """Barrier 2 sits above auto mode: no classifier verdict can open a phase-forbidden write."""
    from app.loop.tools.fs import build_write_file_tool

    clf = FakeClassifier(verdict(False, "looks fine"))
    ctx, _ = ctx_for(classifier=clf, ledger=PhaseLedger(phase=TddPhase.RED))
    res = gate(build_write_file_tool(), ctx, {"file_path": "calculator.py", "content": "x"})
    assert res.behavior == DENY
    assert res.decision_reason["type"] == "phase_rule"
    assert clf.calls == []


def test_a_classifier_denial_offers_permission_denied_hooks_a_retry():
    tool = build_tool(
        name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ran"),
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        check_permissions=lambda a, c: PermissionResult(behavior=ASK, message="ask?"),
    )
    dispatcher = ScriptedDispatcher(script={"PermissionDenied": [AggregatedHookResult(retry=True)]})
    ctx, _ = ctx_for(classifier=FakeClassifier(verdict(True, "risky")), hook_dispatcher=dispatcher)
    call: Any = {"id": "c1", "name": "Bash", "args": {"command": "rm -rf build"}}
    outcome = asyncio.run(run_tool_use(call, AIMessage(content=""), ctx, [tool]))
    assert outcome.message.content == build_yolo_rejection_message("risky")
    assert dispatcher.payload("PermissionDenied")["reason"] == "risky"
    (retry,) = outcome.extra_messages
    assert str(retry.content).startswith("The PermissionDenied hook indicated this command is now approved.")
    assert retry.additional_kwargs == {"is_meta": True}
