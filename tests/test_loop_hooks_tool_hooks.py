"""
Tests for `app/loop/hooks/tool_hooks.py`: the hooks around one tool call.

Ported from `reference/claude-code/src/services/tools/toolHooks.ts` -> `runPreToolUseHooks`,
`runPostToolUseHooks`, `runPostToolUseFailureHooks`. Every branch is driven through a
`ScriptedDispatcher`, so the per-event entry points run their real code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.output import AggregatedHookResult
from app.loop.hooks.tool_hooks import (
    get_rule_behavior_description,
    run_post_tool_use_failure_hooks,
    run_post_tool_use_hooks,
    run_pre_tool_use_hooks,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from tests.hook_doubles import ScriptedDispatcher

BLOCK = HookBlockingError(blocking_error="no writes", command="vet.sh")


def ctx(dispatcher: Any, mode: PermissionMode = PermissionMode.ACCEPT_EDITS) -> Any:
    state = AppState(tool_permission_context=ToolPermissionContext(mode=mode))
    return tool_context_for(AppStateStore(state=state), hook_dispatcher=dispatcher)


def pre(script: list[AggregatedHookResult]) -> Any:
    d = ScriptedDispatcher(script={"PreToolUse": script})
    out = asyncio.run(run_pre_tool_use_hooks(ctx(d), "Bash", {"command": "ls"}, "tu1"))
    return out, d


# ── getRuleBehaviorDescription ──────────────────────────────────────────────

def test_rule_behavior_description():
    assert get_rule_behavior_description("allow") == "allowed"
    assert get_rule_behavior_description("deny") == "denied"
    assert get_rule_behavior_description("ask") == "asked for confirmation for"
    assert get_rule_behavior_description("other") == "asked for confirmation for"


# ── PreToolUse ──────────────────────────────────────────────────────────────

def test_pre_payload_and_mode():
    out, d = pre([])
    assert out.verdict is None and out.attachments == [] and out.stopped is False
    assert out.updated_input is None and out.prevent_continuation is False and out.stop_reason is None
    payload = d.payload("PreToolUse")
    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"] == {"command": "ls"}
    assert payload["tool_use_id"] == "tu1"
    assert payload["permission_mode"] == "acceptEdits"


def test_pre_without_dispatcher_does_nothing():
    c = tool_context_for(AppStateStore())
    out = asyncio.run(run_pre_tool_use_hooks(c, "Bash", {}, "t"))
    assert out.verdict is None and out.attachments == [] and out.stopped is False


def test_pre_mode_absent_when_context_has_no_app_state():
    class Bare:
        hook_dispatcher = ScriptedDispatcher()
        cancel = None

    out = asyncio.run(run_pre_tool_use_hooks(Bare(), "Bash", {}, "t"))
    assert "permission_mode" not in Bare.hook_dispatcher.payload("PreToolUse")
    assert out.stopped is False


def test_pre_blocking_error_is_a_deny_with_the_blocking_message():
    out, _ = pre([AggregatedHookResult(blocking_error=BLOCK)])
    assert out.verdict.behavior == PermissionBehavior.DENY
    assert out.verdict.message == "PreToolUse:Bash hook error: no writes"
    assert out.verdict.updated_input is None


def test_pre_attachment_is_kept():
    att = HookAttachment(type="hook_success", hook_name="h", tool_use_id="t", hook_event="PreToolUse")
    out, _ = pre([AggregatedHookResult(attachment=att)])
    assert out.attachments == [att]


def test_pre_allow_carries_updated_input_and_no_message():
    out, _ = pre([AggregatedHookResult(permission_behavior="allow", updated_input={"command": "pwd"},
                                       hook_permission_decision_reason="ok")])
    assert out.verdict.behavior == PermissionBehavior.ALLOW
    assert out.verdict.updated_input == {"command": "pwd"}
    assert out.verdict.message == ""
    assert out.updated_input is None


def test_pre_allow_without_updated_input():
    out, _ = pre([AggregatedHookResult(permission_behavior="allow")])
    assert out.verdict.updated_input is None


def test_pre_deny_reason_or_default_and_never_updated_input():
    out, _ = pre([AggregatedHookResult(permission_behavior="deny", updated_input={"x": 1},
                                       hook_permission_decision_reason="policy")])
    assert out.verdict.behavior == PermissionBehavior.DENY
    assert out.verdict.message == "policy"
    assert out.verdict.updated_input is None
    out, _ = pre([AggregatedHookResult(permission_behavior="deny")])
    assert out.verdict.message == "Hook PreToolUse:Bash denied this tool"


def test_pre_ask_message_and_updated_input():
    out, _ = pre([AggregatedHookResult(permission_behavior="ask", updated_input={"command": "x"})])
    assert out.verdict.behavior == PermissionBehavior.ASK
    assert out.verdict.message == "Hook PreToolUse:Bash asked for confirmation for this tool"
    assert out.verdict.updated_input == {"command": "x"}
    out, _ = pre([AggregatedHookResult(permission_behavior="ask")])
    assert out.verdict.updated_input is None


def test_pre_passthrough_rewrite_is_not_a_verdict():
    out, _ = pre([AggregatedHookResult(updated_input={"command": "pwd"})])
    assert out.verdict is None
    assert out.updated_input == {"command": "pwd"}


def test_pre_rewrite_alongside_a_verdict_is_not_a_passthrough():
    out, _ = pre([AggregatedHookResult(permission_behavior="allow", updated_input={"command": "pwd"})])
    assert out.updated_input is None


def test_pre_prevent_continuation_and_stop_reason():
    out, _ = pre([AggregatedHookResult(prevent_continuation=True, stop_reason="halt")])
    assert out.prevent_continuation is True and out.stop_reason == "halt"
    assert out.stopped is False
    out, _ = pre([AggregatedHookResult(prevent_continuation=True)])
    assert out.prevent_continuation is True and out.stop_reason is None


def test_pre_additional_context_becomes_an_attachment():
    out, _ = pre([AggregatedHookResult(additional_contexts=("a", "b"))])
    assert out.attachments == [HookAttachment(
        type="hook_additional_context", content=("a", "b"), hook_name="PreToolUse:Bash",
        tool_use_id="tu1", hook_event="PreToolUse")]


def test_pre_cancel_stops_after_the_first_result():
    d = ScriptedDispatcher(script={"PreToolUse": [AggregatedHookResult(), AggregatedHookResult(blocking_error=BLOCK)]})
    c = ctx(d)
    c.cancel.cancel()
    out = asyncio.run(run_pre_tool_use_hooks(c, "Bash", {}, "tu1"))
    assert out.stopped is True
    assert out.verdict is None  # the second result was never consumed
    assert out.attachments == [HookAttachment(
        type="hook_cancelled", hook_name="PreToolUse:Bash", tool_use_id="tu1", hook_event="PreToolUse")]


def test_pre_exception_is_an_error_attachment_and_stops():
    d = ScriptedDispatcher(raises=RuntimeError("boom"))
    out = asyncio.run(run_pre_tool_use_hooks(ctx(d), "Bash", {}, "tu1"))
    assert out.stopped is True
    assert out.attachments == [HookAttachment(
        type="hook_error_during_execution", content="PreToolUse hooks failed", hook_name="PreToolUse:Bash",
        tool_use_id="tu1", hook_event="PreToolUse")]


# ── PostToolUse / PostToolUseFailure ─────────────────────────────────────────

def post(script: list[AggregatedHookResult], *, is_mcp: bool = False, cancelled: bool = False) -> Any:
    d = ScriptedDispatcher(script={"PostToolUse": script})
    c = ctx(d)
    if cancelled:
        c.cancel.cancel()
    out = asyncio.run(run_post_tool_use_hooks(c, "Bash", {"command": "ls"}, "output", "tu1", is_mcp))
    return out, d


def test_post_payload():
    out, d = post([])
    payload = d.payload("PostToolUse")
    assert payload["tool_response"] == "output"
    assert payload["tool_input"] == {"command": "ls"}
    assert payload["tool_use_id"] == "tu1"
    assert payload["permission_mode"] == "acceptEdits"
    assert out.attachments == [] and out.prevent_continuation is False and out.updated_mcp_tool_output is None


def test_post_cancelled_attachment_is_renamed_and_the_rest_continues():
    cancelled = HookAttachment(type="hook_cancelled", hook_name="x", tool_use_id="y", hook_event="z")
    out, _ = post([AggregatedHookResult(attachment=cancelled), AggregatedHookResult(additional_contexts=("c",))])
    assert out.attachments[0] == HookAttachment(
        type="hook_cancelled", hook_name="PostToolUse:Bash", tool_use_id="tu1", hook_event="PostToolUse")
    assert out.attachments[1].type == "hook_additional_context"


def test_post_blocking_error_attachment_is_deduplicated():
    """#31301: a JSON block yields both; only the blocking-error branch's copy survives."""
    dup = HookAttachment(type="hook_blocking_error", hook_name="x", tool_use_id="y", hook_event="z",
                         blocking_error=BLOCK)
    out, _ = post([AggregatedHookResult(attachment=dup), AggregatedHookResult(blocking_error=BLOCK)])
    assert out.attachments == [HookAttachment(
        type="hook_blocking_error", hook_name="PostToolUse:Bash", tool_use_id="tu1",
        hook_event="PostToolUse", blocking_error=BLOCK)]


def test_post_other_attachments_are_kept():
    ok = HookAttachment(type="hook_success", hook_name="x", tool_use_id="y", hook_event="z")
    out, _ = post([AggregatedHookResult(attachment=ok)])
    assert out.attachments == [ok]


def test_post_continue_false_stops_with_a_record_and_returns_early():
    out, _ = post([AggregatedHookResult(prevent_continuation=True, stop_reason="enough"),
                   AggregatedHookResult(additional_contexts=("never",))])
    assert out.prevent_continuation is True
    assert out.attachments == [HookAttachment(
        type="hook_stopped_continuation", message="enough", hook_name="PostToolUse:Bash",
        tool_use_id="tu1", hook_event="PostToolUse")]
    out, _ = post([AggregatedHookResult(prevent_continuation=True)])
    assert out.attachments[0].message == "Execution stopped by PostToolUse hook"


def test_post_additional_context():
    out, _ = post([AggregatedHookResult(additional_contexts=("x",))])
    assert out.attachments == [HookAttachment(
        type="hook_additional_context", content=("x",), hook_name="PostToolUse:Bash",
        tool_use_id="tu1", hook_event="PostToolUse")]


def test_post_mcp_output_only_for_mcp_tools():
    out, _ = post([AggregatedHookResult(updated_mcp_tool_output={"v": 1})], is_mcp=True)
    assert out.updated_mcp_tool_output == {"v": 1}
    out, _ = post([AggregatedHookResult(updated_mcp_tool_output={"v": 1})], is_mcp=False)
    assert out.updated_mcp_tool_output is None


def test_post_a_bad_result_becomes_an_error_attachment_and_the_rest_continues():
    class Bad:
        @property
        def attachment(self) -> Any:
            raise ValueError("bad result")

    items: list[Any] = [Bad(), AggregatedHookResult(additional_contexts=("ok",))]
    d = ScriptedDispatcher(script={"PostToolUse": items})
    out = asyncio.run(run_post_tool_use_hooks(ctx(d), "Bash", {}, "o", "tu1"))
    assert out.attachments[0] == HookAttachment(
        type="hook_error_during_execution", content="bad result", hook_name="PostToolUse:Bash",
        tool_use_id="tu1", hook_event="PostToolUse")
    assert out.attachments[1].content == ("ok",)


def test_post_stream_failure_is_swallowed():
    d = ScriptedDispatcher(raises=RuntimeError("down"))
    out = asyncio.run(run_post_tool_use_hooks(ctx(d), "Bash", {}, "o", "tu1"))
    assert out.attachments == [] and out.prevent_continuation is False


def test_failure_hooks_payload_and_never_stop():
    d = ScriptedDispatcher(script={"PostToolUseFailure": [
        AggregatedHookResult(prevent_continuation=True, stop_reason="x"),
        AggregatedHookResult(blocking_error=BLOCK),
        AggregatedHookResult(updated_mcp_tool_output={"v": 1}),
    ]})
    out = asyncio.run(run_post_tool_use_failure_hooks(ctx(d), "Bash", {"command": "ls"}, "kaboom", "tu1", True))
    payload = d.payload("PostToolUseFailure")
    assert payload["error"] == "kaboom"
    assert payload["is_interrupt"] is True
    assert payload["tool_input"] == {"command": "ls"}
    assert out.prevent_continuation is False
    assert out.updated_mcp_tool_output is None
    assert out.attachments == [HookAttachment(
        type="hook_blocking_error", hook_name="PostToolUseFailure:Bash", tool_use_id="tu1",
        hook_event="PostToolUseFailure", blocking_error=BLOCK)]


def test_failure_hooks_without_interrupt_flag():
    d = ScriptedDispatcher()
    asyncio.run(run_post_tool_use_failure_hooks(ctx(d), "Bash", {}, "e", "tu1"))
    assert "is_interrupt" not in d.payload("PostToolUseFailure")
