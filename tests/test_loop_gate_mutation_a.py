"""
Pre-push gate, cluster A: pins that the Phase 4b mutation run showed were missing
(auto mode, the hooks-schema validator, PermissionDenied hooks, the tool pipeline's catch).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from typing import Any

from langchain_core.messages import AIMessage

from app.loop.context import AppStateStore, DenialTrackingBox, tool_context_for
from app.loop.hooks.config import HooksConfigSnapshot
from app.loop.hooks.execute import HookDispatcher, execute_permission_denied_hooks
from app.loop.hooks.output import AggregatedHookResult
from app.loop.hooks.schemas import CommandHook, HookMatcher, hooks_schema_errors
from app.loop.hooks.tool_hooks import run_permission_denied_hooks
from app.loop.permissions.denial_tracking import DenialTrackingState
from app.loop.permissions.rules import extract_permission_context
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
)
from app.loop.permissions.yolo_classifier import format_action_for_classifier
from app.loop.tools.base import build_tool
from app.loop.tools.execution import run_tool_use
from app.loop.tools.types import ToolResult
from tests.hook_doubles import ScriptedDispatcher
from tests.test_loop_permissions_auto_mode import (
    AUTO,
    AUTO_INTERACTIVE,
    AskTool,
    FakeClassifier,
    ctx_for,
    gate,
    verdict,
)

ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK


class RecordingDispatcher(ScriptedDispatcher):
    """Also records the keyword arguments `execute_hooks` was called with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_hooks(self, hook_input: Any, tool_use_id: str, **kwargs: Any):
        self.calls.append((tool_use_id, kwargs))
        async for item in super().execute_hooks(hook_input, tool_use_id, **kwargs):
            yield item


# ── PermissionDenied hooks ───────────────────────────────────────────────────

def test_permission_denied_hooks_run_only_when_configured_for_the_event(tmp_path):
    script = tmp_path / "retry.py"
    script.write_text(
        "import json; print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PermissionDenied', 'retry': True}}))"
    )
    hook = CommandHook(command=f"{sys.executable} {script}")
    dispatcher = HookDispatcher(
        snapshot=HooksConfigSnapshot(hooks={"PermissionDenied": (HookMatcher(hooks=(hook,)),)}),
        session_id="s", cwd=str(tmp_path), project_dir=str(tmp_path),
    )
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher)
    assert asyncio.run(run_permission_denied_hooks(ctx, "Bash", {"command": "x"}, "t1", "risky")) is True


def test_permission_denied_hooks_are_handed_the_call_and_the_signal():
    dispatcher = RecordingDispatcher(script={"PermissionDenied": [AggregatedHookResult(retry=True)]})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher)
    assert asyncio.run(run_permission_denied_hooks(ctx, "Bash", {}, "tu9", "why")) is True
    [(tool_use_id, kwargs)] = dispatcher.calls
    assert tool_use_id == "tu9"
    assert kwargs == {"match_query": "Bash", "signal": ctx.cancel, "tool_context": ctx}


def test_execute_permission_denied_hooks_passes_the_signal_it_is_given():
    dispatcher = RecordingDispatcher()
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher)
    marker = object()

    async def drain() -> None:
        async for _ in execute_permission_denied_hooks(ctx, "Edit", "t", {}, "r", "auto", marker):
            pass

    asyncio.run(drain())
    assert dispatcher.calls[0][1]["signal"] is marker
    assert dispatcher.payload("PermissionDenied")["permission_mode"] == "auto"


def _denying_tool(reason: dict[str, Any]) -> Any:
    return build_tool(
        name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ran"),
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        check_permissions=lambda a, c: PermissionResult(behavior=DENY, message="no", decision_reason=reason),
    )


def _run(tool: Any, ctx: Any, call_id: str | None = "c1") -> Any:
    call: Any = {"name": "Bash", "args": {"command": "rm -rf build"}}
    if call_id is not None:
        call["id"] = call_id
    return asyncio.run(run_tool_use(call, AIMessage(content=""), ctx, [tool]))


def test_only_an_auto_mode_classifier_denial_offers_a_retry():
    dispatcher = ScriptedDispatcher(script={"PermissionDenied": [AggregatedHookResult(retry=True)]})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher)
    outcome = _run(_denying_tool({"type": "classifier", "classifier": "dangerous-agent-action"}), ctx)
    assert outcome.extra_messages == ()
    assert dispatcher.seen == [] or all(e != "PermissionDenied" for e, _ in dispatcher.seen)


def test_an_auto_mode_denial_hands_the_hooks_the_tool_call_and_a_default_reason():
    dispatcher = ScriptedDispatcher(script={"PermissionDenied": [AggregatedHookResult()]})
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=dispatcher)
    outcome = _run(_denying_tool({"type": "classifier", "classifier": "auto-mode"}), ctx)
    assert outcome.extra_messages == ()
    payload = dispatcher.payload("PermissionDenied")
    assert (payload["tool_name"], payload["tool_use_id"], payload["reason"]) == ("Bash", "c1", "Permission denied")
    assert payload["tool_input"] == {"command": "rm -rf build"}


# ── the tool pipeline's outer catch ──────────────────────────────────────────

def _raising_gate_ctx() -> Any:
    async def block(*args: Any, **kwargs: Any) -> Any:
        return verdict(True, "risky")

    ctx, _ = ctx_for(classifier=block, denial=DenialTrackingState(2, 2))
    return ctx


def _asking_tool() -> Any:
    return build_tool(
        name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ran"),
        check_permissions=lambda a, c: PermissionResult(behavior=ASK, message="ask?"),
    )


def test_the_outer_catch_answers_the_call_it_caught():
    outcome = _run(_asking_tool(), _raising_gate_ctx())
    assert outcome.message.tool_call_id == "c1"
    assert outcome.message.status == "error"


def test_the_outer_catch_tolerates_a_call_without_an_id():
    outcome = _run(_asking_tool(), _raising_gate_ctx(), call_id=None)
    assert outcome.message.tool_call_id == ""
    assert outcome.message.status == "error"


def test_hooks_stopped_by_a_cancel_still_hand_back_their_messages():
    class CancellingDispatcher(ScriptedDispatcher):
        async def execute_hooks(self, hook_input: Any, tool_use_id: str, **kwargs: Any):
            kwargs["tool_context"].cancel.cancel()
            yield AggregatedHookResult(additional_contexts=("mind the tests",))

    tool = build_tool(name="Bash", prompt="p", call=lambda a, c: ToolResult(content="ran"))
    ctx = tool_context_for(AppStateStore(), hook_dispatcher=CancellingDispatcher())
    outcome = _run(tool, ctx)
    assert "STOP what you are doing" in str(outcome.message.content)
    assert [str(m.content) for m in outcome.extra_messages] == [
        "<system-reminder>\nPreToolUse:Bash hook additional context: mind the tests\n</system-reminder>"
    ]


# ── hooks_schema_errors ──────────────────────────────────────────────────────

def test_hooks_schema_errors_reports_every_issue_with_its_path():
    assert hooks_schema_errors("x") == ["hooks: Expected object"]
    document = {
        "NoSuch": [],
        "Stop": "x",
        "PreToolUse": [
            3,
            {"matcher": 1, "hooks": "y"},
            {"hooks": [{"type": "command", "command": None}, {"type": "command", "command": "ok"}, {"type": "bad"}]},
        ],
        "SessionStart": [{"hooks": [{"type": "command", "command": 7}]}],
    }
    assert hooks_schema_errors(document) == [
        "hooks.NoSuch: Invalid key in record",
        "hooks.Stop: Expected array",
        "hooks.PreToolUse.0: Expected object",
        "hooks.PreToolUse.1.matcher: Expected string",
        "hooks.PreToolUse.1.hooks: Expected array",
        "hooks.PreToolUse.2.hooks.0.command: Expected value, received null",
        "hooks.PreToolUse.2.hooks.2: unknown hook type: 'bad'",
        "hooks.SessionStart.0.hooks.0: 'command' must be a string",
    ]
    assert json.dumps(hooks_schema_errors({"Stop": [{"hooks": []}]})) == "[]"


# ── auto mode ────────────────────────────────────────────────────────────────

def test_a_worker_s_streak_resets_in_its_box_on_any_allow():
    tool = build_tool(name="Free", prompt="p", call=lambda a, c: ToolResult(content=""),
                      check_permissions=lambda a, c: PermissionResult(behavior=ALLOW))
    box = DenialTrackingBox(DenialTrackingState(1, 2))
    ctx, store = ctx_for(denial=DenialTrackingState(1, 9), local_denial_tracking=box)
    assert gate(tool, ctx).behavior == ALLOW
    assert box.state == DenialTrackingState(0, 2)
    assert store.get().denial_tracking == DenialTrackingState(1, 9)


def test_an_allow_with_no_streak_recorded_changes_nothing():
    tool = build_tool(name="Free", prompt="p", call=lambda a, c: ToolResult(content=""),
                      check_permissions=lambda a, c: PermissionResult(behavior=ALLOW))
    ctx, store = ctx_for()
    assert gate(tool, ctx).behavior == ALLOW
    assert store.get().denial_tracking is None


def test_a_streak_of_one_is_reset():
    tool = build_tool(name="Free", prompt="p", call=lambda a, c: ToolResult(content=""),
                      check_permissions=lambda a, c: PermissionResult(behavior=ALLOW))
    ctx, store = ctx_for(denial=DenialTrackingState(1, 1))
    gate(tool, ctx)
    assert store.get().denial_tracking == DenialTrackingState(0, 1)


class ArgsTool(AskTool):
    """An AskTool whose fast path and interaction need depend on the real input and context."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.seen_args: list[Any] = []
        self.seen_views: list[Any] = []

    def requires_user_interaction(self, args=None):
        return bool(args and args.get("interactive"))

    def check_permissions(self, input_args, context):
        self.seen_args.append(input_args)
        view = extract_permission_context(context)
        self.seen_views.append(view)
        if view.mode == PermissionMode.ACCEPT_EDITS and input_args.get("fast"):
            return PermissionResult(behavior=ALLOW)
        return PermissionResult(behavior=ASK, message="ask?")


def test_interaction_and_the_fast_path_see_the_real_input():
    clf = FakeClassifier()
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=clf)
    assert gate(ArgsTool(), ctx, {"interactive": True}).behavior == ASK
    tool = ArgsTool()
    ctx, _ = ctx_for(classifier=clf)
    res = gate(tool, ctx, {"fast": True})
    assert res.behavior == ALLOW
    assert tool.seen_args == [{"fast": True}, {"fast": True}]
    assert clf.calls == []


def test_the_fast_path_view_keeps_the_rest_of_the_permission_context():
    tool = ArgsTool()
    ctx, _ = ctx_for(classifier=FakeClassifier(verdict(False)))
    gate(tool, ctx, {"fast": False})
    accept_view = tool.seen_views[1]
    assert accept_view.mode == PermissionMode.ACCEPT_EDITS
    assert accept_view.should_avoid_permission_prompts is True


def test_the_fast_path_overrides_an_explicit_context_too():
    tool = ArgsTool()
    ctx, _ = ctx_for(classifier=FakeClassifier(), permission_context=AUTO)
    assert gate(tool, ctx, {"fast": True}).behavior == ALLOW
    assert tool.seen_views[1].mode == PermissionMode.ACCEPT_EDITS


def test_a_fast_path_that_raises_falls_through_to_the_classifier():
    class Raising(AskTool):
        def check_permissions(self, input_args, context):
            if context.get_app_state().tool_permission_context.mode == PermissionMode.ACCEPT_EDITS:
                raise RuntimeError("boom")
            return PermissionResult(behavior=ASK, message="ask?")

    clf = FakeClassifier(verdict(False, "fine"))
    ctx, _ = ctx_for(classifier=clf)
    assert gate(Raising(), ctx).decision_reason == {"type": "classifier", "classifier": "auto-mode", "reason": "fine"}


def test_an_allowlisted_call_resets_the_streak():
    ctx, store = ctx_for(classifier=FakeClassifier(), denial=DenialTrackingState(2, 3))
    gate(AskTool(name="TodoWrite"), ctx, {"todos": []})
    assert store.get().denial_tracking == DenialTrackingState(0, 3)


def test_the_classifier_is_handed_the_action_tools_and_memory(monkeypatch):
    import app.loop.permissions.yolo_classifier as yc

    monkeypatch.setattr(yc, "cached_tddagents_md", lambda run_id: f"md:{run_id}")
    tool = AskTool()
    clf = FakeClassifier(verdict(False))
    ctx, _ = ctx_for(classifier=clf, tools=(tool,))
    gate(tool, ctx, {"command": "make"})
    _, action, tools, _, _, kwargs = clf.calls[0]
    assert action == format_action_for_classifier("Bash", {"command": "make"})
    assert tools == (tool,)
    assert kwargs == {"tddagents_md": "md:r1"}
    clf = FakeClassifier(verdict(False))
    ctx, _ = ctx_for(classifier=clf)
    gate(tool, dataclasses.replace(ctx, run_id=""))
    assert clf.calls[0][5] == {"tddagents_md": "md:"}


def test_the_total_limit_warning_is_upstreams():
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=FakeClassifier(verdict(True, "bad")),
                     denial=DenialTrackingState(0, 19))
    assert gate(AskTool(), ctx).decision_reason["reason"] == (
        "20 actions were blocked this session. Please review the transcript before continuing."
        "\n\nLatest blocked action: bad"
    )


def test_the_denial_limit_keeps_the_original_classifier_name():
    reason = {"type": "classifier", "classifier": "dangerous-agent-action", "reason": "x"}
    ctx, _ = ctx_for(perm=AUTO_INTERACTIVE, classifier=FakeClassifier(verdict(True, "bad")),
                     denial=DenialTrackingState(2, 2))
    res = gate(AskTool(reason=reason), ctx)
    assert res.decision_reason["classifier"] == "dangerous-agent-action"
