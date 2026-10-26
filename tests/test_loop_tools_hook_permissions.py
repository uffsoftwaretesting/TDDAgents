"""
Hook verdict versus permission rules: `app/loop/tools/hook_permissions.py`.

The invariant upstream states in its own docstring — *hook 'allow' does NOT bypass
settings.json deny/ask rules* — matters more here than there, because the TDD phase rules
live inside the same gate. A PreToolUse hook that approves a write must not be able to put
production code on disk during RED.
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
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool
from app.loop.tools.hook_permissions import (
    HookPermissionVerdict,
    resolve_hook_permission_decision,
)
from tests.hook_doubles import ScriptedDispatcher


async def _unused_call(args: dict[str, Any], ctx: Any) -> Any:  # pragma: no cover
    raise AssertionError("the tool must not run during a permission check")


def make_tool(name: str = "WriteFile", **kwargs: Any) -> Any:
    return build_tool(name=name, prompt="p", call=_unused_call, **kwargs)


def make_context(ledger: PhaseLedger | None = None, **kwargs: Any) -> Any:
    store = AppStateStore(AppState(phase_ledger=ledger)) if ledger is not None else AppStateStore()
    return tool_context_for(
        store,
        permission_context=ToolPermissionContext(should_avoid_permission_prompts=True),
        **kwargs,
    )


#: A path the default (RED) ledger permits, so these tests exercise the hook logic rather
#: than tripping the phase rules — which have their own tests.
NEUTRAL_ARGS = {"file_path": "tests/test_a.py"}


def resolve(verdict, tool=None, args=None, context=None):
    return asyncio.run(
        resolve_hook_permission_decision(
            verdict, tool or make_tool(), args or dict(NEUTRAL_ARGS), context or make_context()
        )
    )


# ── no hook opinion ─────────────────────────────────────────────────────────

def test_without_a_verdict_the_ordinary_gate_decides():
    resolved = resolve(None)
    assert resolved.decision.behavior == PermissionBehavior.ALLOW
    assert resolved.input == NEUTRAL_ARGS


# ── deny ────────────────────────────────────────────────────────────────────

def test_a_hook_deny_is_final_and_keeps_the_original_input():
    verdict = HookPermissionVerdict(
        behavior=PermissionBehavior.DENY, message="blocked by policy",
        updated_input={"file_path": "rewritten.py"},
    )
    resolved = resolve(verdict)
    assert resolved.decision.behavior == PermissionBehavior.DENY
    assert resolved.decision.message == "blocked by policy"
    # Upstream drops a deny's updatedInput: the tool will not run, so it is irrelevant.
    assert resolved.input == NEUTRAL_ARGS


def test_a_deny_without_a_message_still_says_something_useful():
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.DENY))
    assert "WriteFile" in resolved.decision.message


# ── allow, and the re-check that follows it ─────────────────────────────────

def test_a_hook_allow_stands_when_no_rule_objects():
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW))
    assert resolved.decision.behavior == PermissionBehavior.ALLOW
    assert resolved.decision.decision_reason == {"type": "hook", "hookName": "PreToolUse:WriteFile"}


def test_a_hook_allow_is_applied_to_its_own_rewritten_input():
    verdict = HookPermissionVerdict(
        behavior=PermissionBehavior.ALLOW, updated_input={"file_path": "tests/rewritten.py"}
    )
    resolved = resolve(verdict)
    assert resolved.input == {"file_path": "tests/rewritten.py"}
    assert resolved.decision.updated_input == {"file_path": "tests/rewritten.py"}


def test_a_hook_allow_cannot_put_production_code_on_disk_during_red():
    """The invariant, in the form this project actually needs it."""
    tool = make_tool("WriteFile")
    context = make_context(PhaseLedger(phase=TddPhase.RED))
    resolved = resolve(
        HookPermissionVerdict(behavior=PermissionBehavior.ALLOW),
        tool=tool, args={"file_path": "calculator.py"}, context=context,
    )
    assert resolved.decision.behavior == PermissionBehavior.DENY
    assert "RED denies" in resolved.decision.message


def test_a_hook_allow_still_permits_the_write_the_phase_allows():
    context = make_context(PhaseLedger(phase=TddPhase.RED))
    resolved = resolve(
        HookPermissionVerdict(behavior=PermissionBehavior.ALLOW),
        args={"file_path": "tests/test_calc.py"}, context=context,
    )
    assert resolved.decision.behavior == PermissionBehavior.ALLOW


def test_a_gate_deny_overrides_a_hook_allow_and_the_reason_survives():
    async def refuse(args: dict[str, Any], ctx: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.DENY, message="tool says no")

    tool = make_tool(check_permissions=refuse)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), tool=tool)
    assert resolved.decision.behavior == PermissionBehavior.DENY
    assert resolved.decision.message == "tool says no"


# ── ask ─────────────────────────────────────────────────────────────────────

def test_a_hook_ask_leaves_the_decision_to_the_gate():
    async def wants_confirmation(args: dict[str, Any], ctx: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ASK, message="gate asks")

    tool = make_tool(check_permissions=wants_confirmation)
    resolved = resolve(
        HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="hook asks"), tool=tool
    )
    assert resolved.decision.behavior == PermissionBehavior.DENY  # headless: nobody can answer
    assert resolved.input == NEUTRAL_ARGS


def _never_the_full_gate(monkeypatch):
    async def full_gate(tool, args, context, tool_use_id=""):
        raise AssertionError("a hook ask is a forced decision: the full gate must not run")

    monkeypatch.setattr("app.loop.tools.hook_permissions.has_permissions_to_use_tool", full_gate)


def interactive_context(ledger=None, perm=None):
    store = AppStateStore(AppState(phase_ledger=ledger)) if ledger is not None else AppStateStore()
    return tool_context_for(store, permission_context=perm or ToolPermissionContext())


def test_a_hook_ask_is_forced_and_carries_its_own_message(monkeypatch):
    _never_the_full_gate(monkeypatch)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="hook wording"),
                       context=interactive_context())
    assert (resolved.decision.behavior, resolved.decision.message) == (PermissionBehavior.ASK, "hook wording")
    assert resolved.decision.decision_reason == {
        "type": "hook", "hookName": "PreToolUse:WriteFile", "reason": "hook wording"}


def test_a_hook_ask_without_a_message_names_the_hook(monkeypatch):
    _never_the_full_gate(monkeypatch)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK), context=interactive_context())
    assert resolved.decision.message == "Hook 'PreToolUse:WriteFile' requires approval for this WriteFile command"


def test_a_hook_ask_is_denied_where_no_prompt_can_be_shown():
    from app.loop.permissions.messages import (
        auto_reject_message,
        dont_ask_reject_message,
    )

    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="m"))
    assert (resolved.decision.behavior, resolved.decision.message) == (
        PermissionBehavior.DENY, auto_reject_message("WriteFile"))
    dont_ask = interactive_context(perm=ToolPermissionContext(mode=PermissionMode.DONT_ASK))
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="m"), context=dont_ask)
    assert resolved.decision.message == dont_ask_reject_message("WriteFile")


def test_a_hook_ask_never_overrides_a_deny_rule_or_the_phase_tier():
    rule = PermissionRule(tool_name="WriteFile", rule_behavior=PermissionBehavior.DENY)
    ctx = interactive_context(perm=ToolPermissionContext(always_deny_rules=(rule,)))
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK), context=ctx)
    assert resolved.decision.decision_reason == {"type": "rule", "rule": rule}
    red = interactive_context(ledger=PhaseLedger(phase=TddPhase.RED))
    verdict = HookPermissionVerdict(behavior=PermissionBehavior.ASK, updated_input={"file_path": "calc.py"})
    resolved = resolve(verdict, context=red)
    assert resolved.decision.behavior == PermissionBehavior.DENY
    assert resolved.decision.decision_reason["type"] == "phase_rule"
    assert resolved.input == {"file_path": "calc.py"}


def test_a_hook_ask_reaches_permission_request_hooks_when_headless():
    allow = AggregatedHookResult(permission_request_result={"behavior": "allow"})
    ctx = make_context(hook_dispatcher=ScriptedDispatcher(script={"PermissionRequest": [allow]}))
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK), context=ctx)
    assert resolved.decision.behavior == PermissionBehavior.ALLOW
    assert resolved.decision.decision_reason == {"type": "hook", "hookName": "PermissionRequest"}


def test_a_hook_ask_applies_to_its_rewritten_input():
    verdict = HookPermissionVerdict(
        behavior=PermissionBehavior.ASK, updated_input={"file_path": "tests/rewritten.py"}
    )
    assert resolve(verdict).input == {"file_path": "tests/rewritten.py"}


@pytest.mark.parametrize("behavior", [PermissionBehavior.ALLOW, PermissionBehavior.ASK])
def test_a_verdict_with_no_rewrite_uses_the_original_input(behavior):
    assert resolve(HookPermissionVerdict(behavior=behavior)).input == NEUTRAL_ARGS


# ── what the decision records about itself ──────────────────────────────────

HOOK_REASON = {"type": "hook", "hookName": "PreToolUse:WriteFile"}


def test_a_hook_allow_records_that_a_hook_decided_it():
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW))
    assert resolved.decision.decision_reason == HOOK_REASON


def test_a_hook_deny_records_that_a_hook_decided_it():
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.DENY, message="no"))
    assert resolved.decision.decision_reason == HOOK_REASON


def test_the_recorded_hook_name_follows_the_tool():
    tool = make_tool("Bash")
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW),
                       tool=tool, args={"command": "ls"})
    assert resolved.decision.decision_reason == {"type": "hook", "hookName": "PreToolUse:Bash"}


def test_a_rule_override_keeps_the_gates_own_reason_not_the_hooks():
    async def refuse(args: Any, ctx: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.DENY, message="tool says no",
                                decision_reason={"type": "rule", "rule": "Edit(src/**)"})

    tool = make_tool(check_permissions=refuse)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), tool=tool)
    assert resolved.decision.decision_reason == {"type": "rule", "rule": "Edit(src/**)"}


def test_an_overridden_hook_allow_still_reports_the_input_the_hook_asked_for():
    async def refuse(args: Any, ctx: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.DENY, message="no")

    tool = make_tool(check_permissions=refuse)
    verdict = HookPermissionVerdict(behavior=PermissionBehavior.ALLOW,
                                    updated_input={"file_path": "tests/rewritten.py"})
    assert resolve(verdict, tool=tool).input == {"file_path": "tests/rewritten.py"}


def test_the_gate_is_asked_about_the_hooks_input_not_the_models():
    seen: list[dict[str, Any]] = []

    async def record(args: dict[str, Any], ctx: Any) -> PermissionResult:
        seen.append(dict(args))
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    tool = make_tool(check_permissions=record)
    verdict = HookPermissionVerdict(behavior=PermissionBehavior.ASK,
                                    updated_input={"file_path": "tests/rewritten.py"})
    resolve(verdict, tool=tool)
    assert seen == [{"file_path": "tests/rewritten.py"}]


# ── hook allow: re-checked by rules only (upstream checkRuleBasedPermissions) ──

def _write_tool():
    from app.loop.tools.fs import build_write_file_tool

    return build_write_file_tool()


def test_a_hook_allow_skips_a_non_rule_ask_even_when_headless():
    """A default-mode write is an 'ask' no rule produced: the hook's approval skips it."""
    ws = SimpleNamespace(root="/ws")
    ctx = make_context(workspace=ws)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), tool=_write_tool(), context=ctx)
    assert resolved.decision.behavior == PermissionBehavior.ALLOW
    assert resolved.decision.decision_reason == HOOK_REASON


def test_a_hook_allow_still_meets_an_ask_rule_and_then_the_full_gate():
    rule = PermissionRule(tool_name="WriteFile", rule_behavior=PermissionBehavior.ASK)
    perm = ToolPermissionContext(always_ask_rules=(rule,), should_avoid_permission_prompts=True)
    ctx = tool_context_for(AppStateStore(), permission_context=perm)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), context=ctx)
    assert resolved.decision.behavior == PermissionBehavior.DENY
    assert resolved.decision.decision_reason["type"] == "asyncAgent"


def test_a_hook_allow_meets_the_phase_tier_and_safety_checks():
    red = make_context(ledger=PhaseLedger(phase=TddPhase.RED), workspace=SimpleNamespace(root="/ws"))
    verdict = HookPermissionVerdict(behavior=PermissionBehavior.ALLOW, updated_input={"file_path": "calc.py"})
    resolved = resolve(verdict, tool=_write_tool(), context=red)
    assert resolved.decision.decision_reason["type"] == "phase_rule"
    git = HookPermissionVerdict(behavior=PermissionBehavior.ALLOW, updated_input={"file_path": "tests/.git/x"})
    resolved = resolve(git, tool=_write_tool(), context=make_context(workspace=SimpleNamespace(root="/ws")))
    assert resolved.decision.behavior == PermissionBehavior.DENY  # safety ask -> full gate -> headless deny


def test_an_interactive_tool_needs_the_hook_to_supply_the_input():
    interactive = make_tool(
        requires_user_interaction=lambda args: True,
        check_permissions=lambda a, c: PermissionResult(behavior=PermissionBehavior.ASK, message="ask the user"),
    )
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), tool=interactive)
    assert resolved.decision.behavior == PermissionBehavior.DENY  # full gate, headless
    supplied = HookPermissionVerdict(behavior=PermissionBehavior.ALLOW, updated_input={"file_path": "tests/x.py"})
    resolved = resolve(supplied, tool=interactive)
    assert (resolved.decision.behavior, resolved.input) == (PermissionBehavior.ALLOW, {"file_path": "tests/x.py"})


# ── what the resolver hands to the gate (mutation-driven pins) ───────────────

def _spy(monkeypatch, name, result):
    seen: list[tuple[Any, ...]] = []

    async def spy(*args):
        seen.append(args)
        return result

    monkeypatch.setattr(f"app.loop.tools.hook_permissions.{name}", spy)
    return seen


def _resolve_with_id(verdict, tool=None, context=None):
    return asyncio.run(resolve_hook_permission_decision(
        verdict, tool or make_tool(), dict(NEUTRAL_ARGS), context or make_context(), "tu-9"))


DENIED = PermissionResult(behavior=PermissionBehavior.DENY, message="no")
REWRITE = {"file_path": "tests/rewritten.py"}


def test_full_gate_receives_input_and_tool_use_id_on_every_branch(monkeypatch):
    seen = _spy(monkeypatch, "has_permissions_to_use_tool", DENIED)
    resolved = _resolve_with_id(None)
    assert seen[-1][1:] == (NEUTRAL_ARGS, seen[-1][2], "tu-9")
    assert resolved.input == NEUTRAL_ARGS
    no_opinion = _resolve_with_id(HookPermissionVerdict(behavior=None))
    assert no_opinion.decision is DENIED and len(seen) == 2
    interactive = make_tool(requires_user_interaction=lambda args: True)
    resolved = _resolve_with_id(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW), tool=interactive)
    assert (seen[-1][1], seen[-1][3], resolved.input) == (NEUTRAL_ARGS, "tu-9", NEUTRAL_ARGS)
    rule = PermissionRule(tool_name="WriteFile", rule_behavior=PermissionBehavior.ASK)
    ask_rule_ctx = tool_context_for(AppStateStore(), permission_context=ToolPermissionContext(always_ask_rules=(rule,)))
    resolved = _resolve_with_id(HookPermissionVerdict(behavior=PermissionBehavior.ALLOW, updated_input=REWRITE),
                                context=ask_rule_ctx)
    assert (seen[-1][1], seen[-1][3], resolved.input) == (REWRITE, "tu-9", REWRITE)


def test_hook_ask_handler_receives_input_and_tool_use_id(monkeypatch):
    seen = _spy(monkeypatch, "resolve_ask_without_prompt", DENIED)
    resolved = _resolve_with_id(HookPermissionVerdict(behavior=PermissionBehavior.ASK, updated_input=REWRITE))
    forced, _tool, ask_input, _ctx, tool_use_id = seen[-1]
    assert (forced.behavior, ask_input, tool_use_id, resolved.input) == (
        PermissionBehavior.ASK, REWRITE, "tu-9", REWRITE)


def test_check_file_read_permissions_passes_the_input_through(tmp_path):
    from app.loop.permissions.filesystem import check_file_read_permissions

    ctx = tool_context_for(AppStateStore(), permission_context=ToolPermissionContext(),
                           workspace=SimpleNamespace(root=str(tmp_path)))
    res = check_file_read_permissions("path")({"path": "a"}, ctx)
    assert res.updated_input == {"path": "a"}
