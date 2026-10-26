"""
Tests for the runtime permission gate, step for step against upstream's
`hasPermissionsToUseToolInner` / `hasPermissionsToUseTool` (Phase 3), plus the TDD managed
tier above step 1a.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.hooks.output import AggregatedHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.gate import (
    DENIAL_WORKAROUND_GUIDANCE,
    auto_reject_message,
    has_permissions_to_use_tool,
)
from app.loop.permissions.messages import dont_ask_reject_message
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import PermissionResult, ToolResult
from tests.hook_doubles import ScriptedDispatcher

ALLOW, DENY, ASK, PASS = (
    PermissionBehavior.ALLOW,
    PermissionBehavior.DENY,
    PermissionBehavior.ASK,
    PermissionBehavior.PASSTHROUGH,
)
DEFAULT_REQUEST = "The agent requested permissions to use {}, but you haven't granted it yet."


class _Tool:
    """A duck-typed tool: sync `check_permissions`, optional interaction predicate."""

    def __init__(self, name="T", result=None, interaction=None, raises=None):
        self.name = name
        self._result = result if result is not None else PermissionResult(behavior=PASS)
        self._raises = raises
        self.seen: list[Any] = []
        if interaction is not None:
            self.requires_user_interaction = interaction

    def check_permissions(self, input_args, context):
        self.seen.append((input_args, context))
        if self._raises is not None:
            raise self._raises
        return self._result


def _ctx(perm=None, ledger=None, hook_dispatcher=None):
    state = AppState(phase_ledger=ledger) if ledger is not None else AppState()
    return tool_context_for(
        AppStateStore(state=state),
        permission_context=perm if perm is not None else ToolPermissionContext(),
        hook_dispatcher=hook_dispatcher,
    )


def _gate(tool, perm=None, args=None, ctx=None, tool_use_id=""):
    ctx = ctx if ctx is not None else _ctx(perm)
    return asyncio.run(has_permissions_to_use_tool(tool, args if args is not None else {}, ctx, tool_use_id))


def _rule(behavior, tool="T", content=None, source=PermissionRuleSource.PROJECT_SETTINGS):
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content, source=source)


# ── cancellation and the TDD managed tier ────────────────────────────────────

def test_cancelled_context_denies_before_anything_runs():
    tool = _Tool(result=PermissionResult(behavior=ALLOW))
    ctx = _ctx(ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS))
    ctx.cancel.cancel()
    res = _gate(tool, ctx=ctx)
    assert (res.behavior, res.message, res.decision_reason) == (
        DENY, "Operation cancelled.", {"type": "other", "reason": "cancelled"})
    assert tool.seen == []


def test_phase_rule_denies_above_bypass_and_allow_rules():
    tool = build_tool(name="WriteImplementation", prompt="p", call=lambda a, c: ToolResult(content="x"))
    perm = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS,
                                 always_allow_rules=(_rule(ALLOW, "WriteImplementation"),))
    res = _gate(tool, ctx=_ctx(perm, ledger=PhaseLedger(phase=TddPhase.RED)))
    assert res.behavior == DENY
    assert res.message.startswith("TDD phase RED denies implementation-writing tool")
    assert res.decision_reason == {"type": "phase_rule", "phase": TddPhase.RED, "reason": res.message}


def test_phase_rule_denies_a_settings_allowed_write_in_red():
    tool = build_tool(name="WriteFile", prompt="p", call=lambda a, c: ToolResult(content="x"))
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, always_allow_rules=(_rule(ALLOW, "WriteFile"),))
    res = _gate(tool, args={"file_path": "calc.py"}, ctx=_ctx(perm, ledger=PhaseLedger(phase=TddPhase.RED)))
    assert res.behavior == DENY and "calc.py" in res.message


def test_phase_check_fails_closed(monkeypatch):
    def boom(tool, args, ledger):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr("app.loop.permissions.tdd.check_tdd_phase_permission", boom)
    tool = _Tool(result=PermissionResult(behavior=ALLOW))
    res = _gate(tool, ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS))
    assert res.behavior == DENY
    assert res.message == "TDD phase check failed for T: ledger unreadable"
    assert res.decision_reason == {"type": "phase_rule", "phase": None, "reason": res.message}
    assert tool.seen == []


def test_app_state_without_a_ledger_skips_the_phase_tier():
    ctx = tool_context_for(AppStateStore(), permission_context=ToolPermissionContext())
    ctx.get_app_state = lambda: SimpleNamespace()  # type: ignore[assignment,return-value]
    assert _gate(_Tool(result=PermissionResult(behavior=ALLOW)), ctx=ctx).behavior == ALLOW


def test_permitted_phase_passes_through_to_the_rules():
    tool = build_tool(name="WriteFile", prompt="p", call=lambda a, c: ToolResult(content="x"))
    ctx = _ctx(ToolPermissionContext(), ledger=PhaseLedger(phase=TddPhase.RED))
    res = _gate(tool, args={"file_path": "tests/test_a.py"}, ctx=ctx)
    assert res.behavior == ALLOW  # build_tool's default check_permissions allows


# ── 1a / 1b: tool-wide rules ─────────────────────────────────────────────────

def test_tool_wide_deny_rule_beats_bypass():
    rule = _rule(DENY)
    tool = _Tool(result=PermissionResult(behavior=ALLOW))
    res = _gate(tool, ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, always_deny_rules=(rule,)))
    assert (res.behavior, res.message, res.decision_reason) == (
        DENY, "Permission to use T has been denied.", {"type": "rule", "rule": rule})
    assert tool.seen == []


def test_content_deny_rule_is_not_tool_wide():
    rule = _rule(DENY, content="npm publish")
    res = _gate(_Tool(result=PermissionResult(behavior=ALLOW)), ToolPermissionContext(always_deny_rules=(rule,)))
    assert res.behavior == ALLOW


def test_tool_wide_ask_rule_asks_with_the_default_request_message():
    rule = _rule(ASK)
    tool = _Tool(result=PermissionResult(behavior=ALLOW))
    res = _gate(tool, ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS, always_ask_rules=(rule,)))
    assert (res.behavior, res.message, res.decision_reason) == (
        ASK, DEFAULT_REQUEST.format("T"), {"type": "rule", "rule": rule})


# ── 1c: the tool's own check ─────────────────────────────────────────────────

def test_check_permissions_receives_input_and_context():
    tool = _Tool(result=PermissionResult(behavior=ALLOW))
    ctx = _ctx()
    _gate(tool, args={"a": 1}, ctx=ctx)
    assert tool.seen == [({"a": 1}, ctx)]


def test_async_check_permissions_is_awaited():
    async def check(args, ctx):
        return PermissionResult(behavior=DENY, message="async no")

    tool = build_tool(name="A", prompt="p", call=lambda a, c: ToolResult(content="x"), check_permissions=check)
    assert (_gate(tool).behavior, _gate(tool).message) == (DENY, "async no")


def test_check_permissions_exception_is_passthrough_not_deny():
    tool = _Tool(raises=RuntimeError("boom"))
    assert _gate(tool, ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)).behavior == ALLOW
    asked = _gate(tool, ToolPermissionContext())
    assert (asked.behavior, asked.message) == (ASK, DEFAULT_REQUEST.format("T"))


def test_cancelled_error_from_check_permissions_propagates():
    tool = _Tool(raises=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _gate(tool)


def test_non_result_return_is_treated_as_passthrough():
    tool = _Tool(result="not a result")
    assert _gate(tool).behavior == ASK


# ── 1d–1g: bypass-immune outcomes ────────────────────────────────────────────

BYPASS = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)


def test_tool_deny_stands_unchanged():
    result = PermissionResult(behavior=DENY, message="nope", decision_reason={"type": "other", "reason": "r"})
    assert _gate(_Tool(result=result), BYPASS) is result


def test_interaction_ask_is_bypass_immune_for_both_predicate_shapes():
    result = PermissionResult(behavior=ASK, message="interact")
    assert _gate(_Tool(result=result, interaction=lambda a: True), BYPASS) is result
    assert _gate(_Tool(result=result, interaction=lambda: True), BYPASS) is result


def test_interaction_without_an_ask_does_not_invent_one():
    allowed = _gate(_Tool(result=PermissionResult(behavior=ALLOW), interaction=lambda: True), BYPASS)
    assert allowed.behavior == ALLOW


def test_plain_ask_is_bypassed_when_no_immunity_applies():
    plain = PermissionResult(behavior=ASK, message="m")
    assert _gate(_Tool(result=plain, interaction=lambda: False), BYPASS).behavior == ALLOW
    other = PermissionResult(behavior=ASK, decision_reason={"type": "other", "reason": "x"})
    assert _gate(_Tool(result=other), BYPASS).behavior == ALLOW


def test_content_ask_rule_is_bypass_immune():
    rule = _rule(ASK, "Bash", "npm publish:*")
    result = PermissionResult(behavior=ASK, message="publish?", decision_reason={"type": "rule", "rule": rule})
    assert _gate(_Tool(name="Bash", result=result), BYPASS) is result


def test_rule_reason_from_a_non_ask_rule_is_not_immune():
    allow_rule = _rule(ALLOW, "Bash", "x")
    result = PermissionResult(behavior=ASK, decision_reason={"type": "rule", "rule": allow_rule})
    assert _gate(_Tool(name="Bash", result=result), BYPASS).behavior == ALLOW
    not_a_rule = PermissionResult(behavior=ASK, decision_reason={"type": "rule", "rule": "ask"})
    assert _gate(_Tool(result=not_a_rule), BYPASS).behavior == ALLOW


def test_safety_check_ask_is_bypass_immune():
    result = PermissionResult(behavior=ASK, decision_reason={"type": "safetyCheck", "reason": "git dir"})
    assert _gate(_Tool(result=result), BYPASS) is result


# ── 2a / 2b: modes and allow rules ───────────────────────────────────────────

def test_bypass_mode_allows_with_mode_reason_and_fallback_input():
    res = _gate(_Tool(), BYPASS, args={"x": 1})
    assert (res.behavior, res.decision_reason, res.updated_input) == (
        ALLOW, {"type": "mode", "mode": PermissionMode.BYPASS_PERMISSIONS}, {"x": 1})


def test_plan_bypasses_only_when_bypass_is_available():
    available = ToolPermissionContext(mode=PermissionMode.PLAN, is_bypass_permissions_mode_available=True)
    res = _gate(_Tool(), available)
    assert (res.behavior, res.decision_reason) == (ALLOW, {"type": "mode", "mode": PermissionMode.PLAN})
    assert _gate(_Tool(), ToolPermissionContext(mode=PermissionMode.PLAN)).behavior == ASK


def test_bypass_availability_defaults_off_as_upstream():
    assert ToolPermissionContext().is_bypass_permissions_mode_available is False


def test_tool_wide_allow_rule_allows():
    rule = _rule(ALLOW)
    res = _gate(_Tool(), ToolPermissionContext(always_allow_rules=(rule,)), args={"y": 2})
    assert (res.behavior, res.decision_reason, res.updated_input) == (
        ALLOW, {"type": "rule", "rule": rule}, {"y": 2})


def test_updated_input_from_the_tool_check_survives_every_allow_path():
    upd = {"command": "rewritten"}
    base = PermissionResult(behavior=PASS, updated_input=upd)
    for perm in (BYPASS, ToolPermissionContext(always_allow_rules=(_rule(ALLOW),))):
        assert _gate(_Tool(result=base), perm, args={"command": "x"}).updated_input == upd


def test_accept_edits_and_plan_do_not_allow_by_themselves():
    """Upstream's inner gate has no read-only shortcut: the tool's own check decides."""
    for mode in (PermissionMode.ACCEPT_EDITS, PermissionMode.PLAN, PermissionMode.DEFAULT):
        assert _gate(_Tool(), ToolPermissionContext(mode=mode)).behavior == ASK


def test_a_tool_allow_stands_in_default_mode():
    result = PermissionResult(behavior=ALLOW, updated_input={"z": 1})
    assert _gate(_Tool(result=result)) is result


# ── 3: passthrough becomes ask ───────────────────────────────────────────────

def test_passthrough_becomes_ask_with_the_reason_message_and_input():
    reason = {"type": "other", "reason": "needs a look"}
    res = _gate(_Tool(result=PermissionResult(behavior=PASS, message="m", decision_reason=reason,
                                              updated_input={"q": 1})))
    assert (res.behavior, res.message, res.decision_reason, res.updated_input) == (
        ASK, "needs a look", reason, {"q": 1})


def test_passthrough_without_reason_uses_the_default_request_message():
    res = _gate(_Tool(name="Z"))
    assert (res.behavior, res.message, res.decision_reason) == (ASK, DEFAULT_REQUEST.format("Z"), None)


# ── outer gate: dontAsk, headless, PermissionRequest hooks ───────────────────

def test_reject_message_texts():
    assert DENIAL_WORKAROUND_GUIDANCE.startswith("IMPORTANT: You *may* attempt to accomplish this action")
    assert DENIAL_WORKAROUND_GUIDANCE.endswith("Let the user decide how to proceed.")
    assert auto_reject_message("Bash") == f"Permission to use Bash has been denied. {DENIAL_WORKAROUND_GUIDANCE}"
    assert dont_ask_reject_message("Bash") == (
        "Permission to use Bash has been denied because the agent is running in don't ask mode. "
        f"{DENIAL_WORKAROUND_GUIDANCE}")


def test_interactive_context_keeps_the_ask():
    res = _gate(_Tool(result=PermissionResult(behavior=ASK, message="m")))
    assert (res.behavior, res.message) == (ASK, "m")


def test_dont_ask_denies_before_headless():
    perm = ToolPermissionContext(mode=PermissionMode.DONT_ASK, should_avoid_permission_prompts=True)
    res = _gate(_Tool(), perm)
    assert (res.behavior, res.message, res.decision_reason) == (
        DENY, dont_ask_reject_message("T"), {"type": "mode", "mode": PermissionMode.DONT_ASK})


def test_headless_without_hooks_denies_unresolved_asks():
    perm = ToolPermissionContext(should_avoid_permission_prompts=True)
    for behavior in (PASS, ASK):
        res = _gate(_Tool(result=PermissionResult(behavior=behavior)), perm)
        assert (res.behavior, res.message, res.decision_reason) == (
            DENY, auto_reject_message("T"),
            {"type": "asyncAgent", "reason": "Permission prompts are not available in this context"})


def test_headless_does_not_touch_allow_or_deny():
    perm = ToolPermissionContext(should_avoid_permission_prompts=True)
    assert _gate(_Tool(result=PermissionResult(behavior=ALLOW)), perm).behavior == ALLOW
    denied = PermissionResult(behavior=DENY, message="m")
    assert _gate(_Tool(result=denied), perm) is denied


class _Dispatcher(ScriptedDispatcher):
    """Scripts one PermissionRequest decision; `calls` is what each hook was handed."""

    def __init__(self, decision=None, raises=None):
        script = {"PermissionRequest": [AggregatedHookResult(permission_request_result=decision)]}
        super().__init__(script=script, raises=raises)

    @property
    def calls(self) -> list[tuple[Any, ...]]:
        return [
            (event, p["tool_name"], p["tool_input"], p.get("permission_mode"))
            for event, p in self.seen
        ]


HEADLESS = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)


def test_headless_permission_request_hook_allow_and_payload():
    dispatcher = _Dispatcher({"behavior": "allow", "updatedInput": {"command": "safe"}})
    ctx = _ctx(HEADLESS, hook_dispatcher=dispatcher)
    res = _gate(_Tool(name="Bash"), args={"command": "x"}, ctx=ctx, tool_use_id="tu1")
    assert (res.behavior, res.updated_input, res.decision_reason) == (
        ALLOW, {"command": "safe"}, {"type": "hook", "hookName": "PermissionRequest"})
    assert dispatcher.calls == [("PermissionRequest", "Bash", {"command": "x"}, "acceptEdits")]


def test_headless_permission_request_hook_deny():
    ctx = _ctx(HEADLESS, hook_dispatcher=_Dispatcher({"behavior": "deny", "message": "not now"}))
    res = _gate(_Tool(), ctx=ctx)
    assert (res.behavior, res.message) == (DENY, "not now")


def test_headless_hook_without_decision_falls_back_to_auto_deny():
    ctx = _ctx(HEADLESS, hook_dispatcher=_Dispatcher(None))
    assert _gate(_Tool(), ctx=ctx).message == auto_reject_message("T")


def test_permission_request_hooks_never_see_a_phase_denial():
    dispatcher = _Dispatcher({"behavior": "allow"})
    ctx = _ctx(HEADLESS, ledger=PhaseLedger(phase=TddPhase.RED), hook_dispatcher=dispatcher)
    tool = build_tool(name="WriteFile", prompt="p", call=lambda a, c: ToolResult(content="x"))
    res = _gate(tool, args={"file_path": "app.py"}, ctx=ctx)
    assert res.behavior == DENY and res.decision_reason["type"] == "phase_rule"
    assert dispatcher.calls == []


def test_interactive_contexts_do_not_run_permission_request_hooks():
    dispatcher = _Dispatcher({"behavior": "allow"})
    assert _gate(_Tool(), ctx=_ctx(ToolPermissionContext(), hook_dispatcher=dispatcher)).behavior == ASK
    assert dispatcher.calls == []
