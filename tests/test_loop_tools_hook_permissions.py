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

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionResult,
    ToolPermissionContext,
)
from app.loop.tools.base import build_tool
from app.loop.tools.hook_permissions import (
    HookPermissionVerdict,
    resolve_hook_permission_decision,
    verdict_from_hook_outcome,
)


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


# ── reading a dispatcher outcome ────────────────────────────────────────────

def test_a_veto_reads_as_deny():
    outcome = SimpleNamespace(denied=True, reason="no writes today", updated_input=None)
    verdict = verdict_from_hook_outcome(outcome)
    assert verdict is not None
    assert verdict.behavior == PermissionBehavior.DENY
    assert verdict.message == "no writes today"


def test_an_explicit_behavior_string_is_understood():
    outcome = SimpleNamespace(denied=False, permission_behavior="allow", reason="", updated_input=None)
    verdict = verdict_from_hook_outcome(outcome)
    assert verdict is not None and verdict.behavior == PermissionBehavior.ALLOW


def test_an_unknown_behavior_string_is_not_an_opinion():
    outcome = SimpleNamespace(denied=False, permission_behavior="maybe", reason="", updated_input=None)
    assert verdict_from_hook_outcome(outcome) is None


def test_silence_is_not_an_opinion():
    assert verdict_from_hook_outcome(None) is None
    assert verdict_from_hook_outcome(SimpleNamespace(denied=False, updated_input={"a": 1})) is None


def test_an_updated_input_is_carried_with_the_verdict():
    outcome = SimpleNamespace(denied=False, permission_behavior="allow", reason="",
                              updated_input={"file_path": "b.py"})
    verdict = verdict_from_hook_outcome(outcome)
    assert verdict is not None and verdict.updated_input == {"file_path": "b.py"}


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


def test_a_hook_ask_carries_its_own_message_when_the_gate_also_asks(monkeypatch):
    async def asking(tool, args, context):
        return PermissionResult(behavior=PermissionBehavior.ASK, message="gate wording")

    monkeypatch.setattr("app.loop.tools.hook_permissions.has_permissions_to_use_tool", asking)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="hook wording"))
    assert resolved.decision.behavior == PermissionBehavior.ASK
    assert resolved.decision.message == "hook wording"


def test_a_hook_ask_without_a_message_keeps_the_gates_wording(monkeypatch):
    async def asking(tool, args, context):
        return PermissionResult(behavior=PermissionBehavior.ASK, message="gate wording")

    monkeypatch.setattr("app.loop.tools.hook_permissions.has_permissions_to_use_tool", asking)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK))
    assert resolved.decision.message == "gate wording"


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


def test_a_hook_ask_preserves_the_gates_rewritten_input(monkeypatch):
    async def asking(tool, args, context):
        return PermissionResult(behavior=PermissionBehavior.ASK, message="gate",
                                updated_input={"file_path": "tests/gate.py"})

    monkeypatch.setattr("app.loop.tools.hook_permissions.has_permissions_to_use_tool", asking)
    resolved = resolve(HookPermissionVerdict(behavior=PermissionBehavior.ASK, message="hook"))
    assert resolved.decision.updated_input == {"file_path": "tests/gate.py"}
    assert resolved.decision.decision_reason == HOOK_REASON
