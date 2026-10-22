"""Parsing hook output and turning it into a result (`app/loop/hooks/output.py`)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.output import (
    EXPECTED_SCHEMA_HINT,
    AggregatedHookResult,
    HookOutputError,
    HookResult,
    get_pre_tool_hook_blocking_message,
    get_stop_hook_message,
    get_user_prompt_submit_hook_blocking_message,
    hook_json_errors,
    hook_result_from_json,
    is_async_hook_json_output,
    parse_hook_output,
    parse_http_hook_output,
    process_hook_json_output,
    validate_hook_json,
)
from app.loop.hooks.schemas import CommandHook

HOOK = CommandHook(command="vet.sh")


def process(doc: dict[str, Any], **kw: Any):
    defaults: dict[str, Any] = dict(command="vet.sh", hook_name="PreToolUse:Bash", tool_use_id="t1",
                                    hook_event="PreToolUse")
    defaults.update(kw)
    return process_hook_json_output(doc, **defaults)


# ── schema validation ─────────────────────────────────────────────────────────

def test_async_detection():
    assert is_async_hook_json_output({"async": True}) is True
    assert is_async_hook_json_output({"async": 1}) is False
    assert is_async_hook_json_output({"async": False}) is False
    assert is_async_hook_json_output({}) is False


def test_async_union():
    assert hook_json_errors({"async": True}) == []
    assert hook_json_errors({"async": True, "asyncTimeout": 5}) == []
    assert hook_json_errors({"async": True, "asyncTimeout": 1.5}) == []
    # An invalid async response falls through to the sync schema, which ignores the keys.
    assert hook_json_errors({"async": True, "asyncTimeout": "5"}) == []
    assert hook_json_errors({"async": True, "asyncTimeout": True, "continue": "x"}) == ["continue: Expected boolean"]


def test_invalid_async_is_parsed_as_sync_without_the_async_keys():
    parsed, err = validate_hook_json('{"async": true, "asyncTimeout": "x", "reason": "r"}')
    assert err is None
    assert parsed == {"reason": "r"}
    parsed, _ = validate_hook_json('{"async": true, "asyncTimeout": 3}')
    assert parsed == {"async": True, "asyncTimeout": 3}
    parsed, _ = validate_hook_json('{"async": false}')
    assert parsed == {}


def test_non_object():
    assert hook_json_errors([1]) == [": Expected object"]
    assert hook_json_errors("x") == [": Expected object"]


@pytest.mark.parametrize("key, bad, message", [
    ("continue", "yes", "continue: Expected boolean"),
    ("suppressOutput", 1, "suppressOutput: Expected boolean"),
    ("stopReason", 1, "stopReason: Expected string"),
    ("decision", "allow", 'decision: Expected "approve" | "block"'),
    ("reason", [], "reason: Expected string"),
    ("systemMessage", {}, "systemMessage: Expected string"),
])
def test_top_level_types(key, bad, message):
    assert hook_json_errors({key: bad}) == [message]


def test_valid_top_level_and_none_values():
    doc = {"continue": False, "suppressOutput": True, "stopReason": "s", "decision": "block",
           "reason": "r", "systemMessage": "m"}
    assert hook_json_errors(doc) == []
    assert hook_json_errors({"decision": "approve"}) == []
    assert hook_json_errors({"continue": None, "reason": None}) == []


def test_errors_are_reported_together_in_order():
    assert hook_json_errors({"reason": 1, "continue": 2}) == ["continue: Expected boolean", "reason: Expected string"]


@pytest.mark.parametrize("specific, message", [
    ("x", "hookSpecificOutput: Expected object"),
    ({}, "hookSpecificOutput.hookEventName: Invalid input"),
    ({"hookEventName": "Bogus"}, "hookSpecificOutput.hookEventName: Invalid input"),
    ({"hookEventName": 1}, "hookSpecificOutput.hookEventName: Invalid input"),
    ({"hookEventName": "PreToolUse", "permissionDecision": "maybe"},
     'hookSpecificOutput.permissionDecision: Expected "allow" | "deny" | "ask"'),
    ({"hookEventName": "PreToolUse", "permissionDecisionReason": 1},
     "hookSpecificOutput.permissionDecisionReason: Expected string"),
    ({"hookEventName": "PreToolUse", "updatedInput": []}, "hookSpecificOutput.updatedInput: Expected object"),
    ({"hookEventName": "PreToolUse", "additionalContext": 1}, "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "UserPromptSubmit", "additionalContext": 1},
     "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "SessionStart", "initialUserMessage": 1},
     "hookSpecificOutput.initialUserMessage: Expected string"),
    ({"hookEventName": "SessionStart", "watchPaths": "/a"}, "hookSpecificOutput.watchPaths: Expected array of strings"),
    ({"hookEventName": "SessionStart", "watchPaths": [1]}, "hookSpecificOutput.watchPaths: Expected array of strings"),
    ({"hookEventName": "Setup", "additionalContext": 1}, "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "SubagentStart", "additionalContext": 1},
     "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "PostToolUse", "additionalContext": 1}, "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "PostToolUseFailure", "additionalContext": 1},
     "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "PermissionDenied", "retry": "y"}, "hookSpecificOutput.retry: Expected boolean"),
    ({"hookEventName": "Notification", "additionalContext": 1},
     "hookSpecificOutput.additionalContext: Expected string"),
    ({"hookEventName": "PermissionRequest"}, "hookSpecificOutput.decision: Required"),
    ({"hookEventName": "PermissionRequest", "decision": "allow"}, "hookSpecificOutput.decision: Expected object"),
    ({"hookEventName": "PermissionRequest", "decision": {"behavior": "ask"}},
     'hookSpecificOutput.decision: behavior: Expected "allow" | "deny"'),
    ({"hookEventName": "PermissionRequest", "decision": {"behavior": "allow", "updatedInput": 1}},
     "hookSpecificOutput.decision: updatedInput: Expected object"),
    ({"hookEventName": "PermissionRequest", "decision": {"behavior": "allow", "updatedPermissions": [{"x": 1}]}},
     "hookSpecificOutput.decision: updatedPermissions: Invalid permission update"),
    ({"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": 1}},
     "hookSpecificOutput.decision: message: Expected string"),
    ({"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "interrupt": "y"}},
     "hookSpecificOutput.decision: interrupt: Expected boolean"),
    ({"hookEventName": "Elicitation", "action": "maybe"},
     'hookSpecificOutput.action: Expected "accept" | "decline" | "cancel"'),
    ({"hookEventName": "ElicitationResult", "content": "x"}, "hookSpecificOutput.content: Expected object"),
    ({"hookEventName": "CwdChanged", "watchPaths": "x"}, "hookSpecificOutput.watchPaths: Expected array of strings"),
    ({"hookEventName": "FileChanged", "watchPaths": "x"}, "hookSpecificOutput.watchPaths: Expected array of strings"),
    ({"hookEventName": "WorktreeCreate"}, "hookSpecificOutput.worktreePath: Required"),
    ({"hookEventName": "WorktreeCreate", "worktreePath": 1}, "hookSpecificOutput.worktreePath: Expected string"),
])
def test_hook_specific_errors(specific, message):
    assert hook_json_errors({"hookSpecificOutput": specific}) == [message]


@pytest.mark.parametrize("specific", [
    {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "r",
     "updatedInput": {"a": 1}, "additionalContext": "c"},
    {"hookEventName": "PreToolUse", "permissionDecision": "deny"},
    {"hookEventName": "PreToolUse", "permissionDecision": "ask"},
    {"hookEventName": "SessionStart", "watchPaths": ["/a"], "initialUserMessage": "hi"},
    {"hookEventName": "PostToolUse", "updatedMCPToolOutput": [1, 2]},
    {"hookEventName": "PermissionDenied", "retry": True},
    {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow", "updatedInput": {}}},
    {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow", "updatedPermissions": []}},
    {"hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": "m", "interrupt": True}},
    {"hookEventName": "Elicitation", "action": "accept", "content": {}},
    {"hookEventName": "Elicitation", "action": "decline"},
    {"hookEventName": "Elicitation", "action": "cancel"},
    {"hookEventName": "WorktreeCreate", "worktreePath": "/w"},
    {"hookEventName": "Notification"},
])
def test_valid_hook_specific(specific):
    assert hook_json_errors({"hookSpecificOutput": specific}) == []


def test_hook_specific_none_is_ignored():
    assert hook_json_errors({"hookSpecificOutput": None}) == []


# ── parsing ───────────────────────────────────────────────────────────────────

def test_validate_hook_json_error_text():
    parsed, err = validate_hook_json('{"continue": "x"}')
    assert parsed is None
    assert err == (
        'Hook JSON output validation failed:\n  - continue: Expected boolean\n\n'
        'The hook\'s output was: {\n  "continue": "x"\n}'
    )


def test_validate_hook_json_raises_on_bad_json():
    with pytest.raises(ValueError):
        validate_hook_json("{oops")


def test_plain_text_output():
    out = parse_hook_output("  hello\n")
    assert (out.json, out.plain_text, out.validation_error) == (None, "  hello\n", None)


def test_json_output():
    out = parse_hook_output('  {"reason": "r"}  ')
    assert (out.json, out.plain_text, out.validation_error) == ({"reason": "r"}, None, None)


def test_unparseable_json_is_plain_text():
    out = parse_hook_output("{not json")
    assert (out.json, out.plain_text, out.validation_error) == (None, "{not json", None)


def test_invalid_json_carries_the_schema_hint():
    out = parse_hook_output('{"decision": "maybe"}')
    assert out.json is None
    assert out.plain_text == '{"decision": "maybe"}'
    _, err = validate_hook_json('{"decision": "maybe"}')
    assert out.validation_error == f"{err}\n\nExpected schema:\n{json.dumps(EXPECTED_SCHEMA_HINT, indent=2)}"
    assert '"for PreToolUse"' in out.validation_error


def test_schema_hint_pinned():
    assert EXPECTED_SCHEMA_HINT["decision"] == '"approve" | "block" (optional)'
    assert EXPECTED_SCHEMA_HINT["hookSpecificOutput"]["for UserPromptSubmit"]["additionalContext"] == (
        "string (required)")
    assert list(EXPECTED_SCHEMA_HINT) == ["continue", "suppressOutput", "stopReason", "decision", "reason",
                                          "systemMessage", "permissionDecision", "hookSpecificOutput"]


def test_http_empty_body_is_empty_json():
    for body in ("", "  \n"):
        out = parse_http_hook_output(body)
        assert (out.json, out.validation_error) == ({}, None)


def test_http_non_json_body():
    out = parse_http_hook_output(" ok ")
    assert out.json is None
    assert out.validation_error == "HTTP hook must return JSON, but got non-JSON response body: ok"


def test_http_long_non_json_body_is_truncated_at_200():
    exact = "x" * 200
    assert str(parse_http_hook_output(exact).validation_error).endswith(": " + exact)
    long = "y" * 201
    assert parse_http_hook_output(long).validation_error == (
        "HTTP hook must return JSON, but got non-JSON response body: " + "y" * 200 + "…")


def test_http_bad_json():
    out = parse_http_hook_output("{oops")
    assert out.json is None
    assert str(out.validation_error).startswith("HTTP hook must return valid JSON, but parsing failed: ")


def test_http_valid_and_invalid_json():
    assert parse_http_hook_output('{"reason": "r"}').json == {"reason": "r"}
    out = parse_http_hook_output('{"continue": 1}')
    _, err = validate_hook_json('{"continue": 1}')
    assert (out.json, out.validation_error) == (None, err)
    assert "Expected schema" not in str(out.validation_error)


# ── processHookJSONOutput ─────────────────────────────────────────────────────

def test_continue_false_and_stop_reason():
    partial, _ = process({"continue": False, "stopReason": "enough"})
    assert (partial.prevent_continuation, partial.stop_reason) == (True, "enough")
    partial, _ = process({"continue": False})
    assert (partial.prevent_continuation, partial.stop_reason) == (True, None)
    partial, _ = process({"continue": True, "stopReason": "x"})
    assert (partial.prevent_continuation, partial.stop_reason) == (False, None)
    partial, _ = process({})
    assert (partial.prevent_continuation, partial.stop_reason, partial.permission_behavior) == (False, None, None)


def test_legacy_decision():
    partial, att = process({"decision": "approve", "reason": "fine"})
    assert (partial.permission_behavior, partial.blocking_error, partial.hook_permission_decision_reason) == (
        "allow", None, "fine")
    assert att.type == "hook_success"
    partial, att = process({"decision": "block", "reason": "nope"})
    assert partial.permission_behavior == "deny"
    assert partial.blocking_error == HookBlockingError(blocking_error="nope", command="vet.sh")
    assert att == HookAttachment(type="hook_blocking_error", hook_name="PreToolUse:Bash", tool_use_id="t1",
                                 hook_event="PreToolUse", blocking_error=partial.blocking_error)
    partial, _ = process({"decision": "block"})
    assert partial.blocking_error.blocking_error == "Blocked by hook"
    with pytest.raises(HookOutputError) as exc:
        process({"decision": "maybe"})
    assert str(exc.value) == "Unknown hook decision type: maybe. Valid types are: approve, block"


def test_reason_without_a_decision_sets_no_reason():
    partial, _ = process({"reason": "r"})
    assert partial.hook_permission_decision_reason is None


def test_system_message():
    assert process({"systemMessage": "warn"})[0].system_message == "warn"
    assert process({"systemMessage": ""})[0].system_message is None


def test_pre_tool_use_permission_decisions():
    def spec(**kw: Any) -> dict[str, Any]:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **kw}}

    partial, _ = process(spec(permissionDecision="allow", permissionDecisionReason="ok"))
    assert (partial.permission_behavior, partial.hook_permission_decision_reason, partial.blocking_error) == (
        "allow", "ok", None)
    partial, _ = process(spec(permissionDecision="ask"))
    assert (partial.permission_behavior, partial.hook_permission_decision_reason) == ("ask", None)
    partial, att = process(spec(permissionDecision="deny", permissionDecisionReason="specific"))
    assert partial.permission_behavior == "deny"
    assert partial.blocking_error == HookBlockingError(blocking_error="specific", command="vet.sh")
    assert att.type == "hook_blocking_error"
    # Reason precedence for a deny: permissionDecisionReason, then reason, then the default.
    partial, _ = process({**spec(permissionDecision="deny"), "reason": "top"})
    assert partial.blocking_error.blocking_error == "top"
    assert partial.hook_permission_decision_reason is None
    partial, _ = process(spec(permissionDecision="deny"))
    assert partial.blocking_error.blocking_error == "Blocked by hook"
    with pytest.raises(HookOutputError) as exc:
        process(spec(permissionDecision="maybe"))
    assert str(exc.value) == "Unknown hook permissionDecision type: maybe. Valid types are: allow, deny, ask"


def test_pre_tool_use_specific_overrides_legacy_decision():
    doc = {"decision": "block", "reason": "legacy",
           "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
    partial, _ = process(doc)
    assert partial.permission_behavior == "allow"
    # The legacy block's error is kept, as upstream: only a specific deny rewrites it.
    assert partial.blocking_error.blocking_error == "legacy"


def test_pre_tool_use_updated_input_and_context():
    doc = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "ls"},
                                  "additionalContext": "ctx"}}
    partial, _ = process(doc)
    assert (partial.updated_input, partial.additional_context, partial.permission_behavior) == (
        {"command": "ls"}, "ctx", None)
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {}}})
    assert partial.updated_input is None


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Setup", "SubagentStart", "PostToolUseFailure"])
def test_additional_context_events(event):
    partial, _ = process({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "c"}},
                         hook_event=event)
    assert partial.additional_context == "c"


def test_session_start_fields():
    doc = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "c",
                                  "initialUserMessage": "hi", "watchPaths": ["/a", "/b"]}}
    partial, _ = process(doc, hook_event="SessionStart")
    assert (partial.additional_context, partial.initial_user_message, partial.watch_paths) == (
        "c", "hi", ("/a", "/b"))
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "SessionStart"}}, hook_event="SessionStart")
    assert partial.watch_paths == ()


def test_post_tool_use_fields():
    doc = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "c",
                                  "updatedMCPToolOutput": {"x": 1}}}
    partial, _ = process(doc, hook_event="PostToolUse")
    assert (partial.additional_context, partial.updated_mcp_tool_output) == ("c", {"x": 1})
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": 0}},
                         hook_event="PostToolUse")
    assert partial.updated_mcp_tool_output is None


def test_permission_denied_retry():
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PermissionDenied", "retry": True}},
                         hook_event="PermissionDenied")
    assert partial.retry is True


def test_permission_request_decision():
    allow = {"behavior": "allow", "updatedInput": {"command": "safe"}}
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": allow}},
                         hook_event="PermissionRequest")
    assert (partial.permission_request_result, partial.permission_behavior, partial.updated_input) == (
        allow, "allow", {"command": "safe"})
    deny = {"behavior": "deny", "updatedInput": {"command": "x"}}
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": deny}},
                         hook_event="PermissionRequest")
    assert (partial.permission_behavior, partial.updated_input) == ("deny", None)
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                 "decision": {"behavior": "allow", "updatedInput": {}}}},
                         hook_event="PermissionRequest")
    assert partial.updated_input is None
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {}}},
                         hook_event="PermissionRequest")
    assert (partial.permission_request_result, partial.permission_behavior) == (None, None)


def test_elicitation_decline():
    partial, att = process({"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "decline"}},
                           hook_event="Elicitation")
    assert partial.blocking_error.blocking_error == "Elicitation denied by hook"
    assert att.type == "hook_blocking_error"
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "ElicitationResult", "action": "decline"}},
                         hook_event="ElicitationResult")
    assert partial.blocking_error.blocking_error == "Elicitation result blocked by hook"
    partial, _ = process({"reason": "why", "hookSpecificOutput": {"hookEventName": "Elicitation",
                                                                  "action": "decline"}}, hook_event="Elicitation")
    assert partial.blocking_error.blocking_error == "why"
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "Elicitation", "action": "accept"}},
                         hook_event="Elicitation")
    assert partial.blocking_error is None


def test_unhandled_event_sets_nothing():
    partial, _ = process({"hookSpecificOutput": {"hookEventName": "Notification", "additionalContext": "c"}},
                         hook_event="Notification")
    assert partial.additional_context is None


def test_expected_event_mismatch():
    doc = {"hookSpecificOutput": {"hookEventName": "PostToolUse"}}
    with pytest.raises(HookOutputError) as exc:
        process(doc, expected_hook_event="PreToolUse")
    assert str(exc.value) == (
        "Hook returned incorrect event name: expected 'PreToolUse' but got 'PostToolUse'. Full stdout: "
        + json.dumps(doc, indent=2))
    process(doc, expected_hook_event="PostToolUse")
    process(doc)  # no expectation, no check


def test_success_attachment_shape():
    _, att = process({}, stdout="out", stderr="err", exit_code=0, duration_ms=12)
    assert att == HookAttachment(type="hook_success", hook_name="PreToolUse:Bash", tool_use_id="t1",
                                 hook_event="PreToolUse", content="", stdout="out", stderr="err", exit_code=0,
                                 command="vet.sh", duration_ms=12)


def test_hook_result_from_json():
    doc = {"continue": False, "stopReason": "s", "systemMessage": "m",
           "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                  "permissionDecisionReason": "r", "updatedInput": {"a": 1},
                                  "additionalContext": "c"}}
    result = hook_result_from_json(HOOK, doc, command="vet.sh", hook_name="PreToolUse:Bash", tool_use_id="t1",
                                   hook_event="PreToolUse")
    assert result.hook is HOOK
    assert result.outcome == "success"
    assert result.attachment is not None and result.attachment.type == "hook_success"
    assert (result.prevent_continuation, result.stop_reason, result.system_message) == (True, "s", "m")
    assert (result.permission_behavior, result.hook_permission_decision_reason) == ("ask", "r")
    assert (result.updated_input, result.additional_context) == ({"a": 1}, "c")
    session = hook_result_from_json(
        HOOK, {"hookSpecificOutput": {"hookEventName": "SessionStart", "initialUserMessage": "i",
                                      "watchPaths": ["/w"]}},
        command="c", hook_name="SessionStart", tool_use_id="t", hook_event="SessionStart")
    assert (session.initial_user_message, session.watch_paths) == ("i", ("/w",))
    post = hook_result_from_json(
        HOOK, {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": "o"}},
        command="c", hook_name="h", tool_use_id="t", hook_event="PostToolUse")
    assert post.updated_mcp_tool_output == "o"
    req = {"behavior": "deny"}
    perm = hook_result_from_json(
        HOOK, {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": req}},
        command="c", hook_name="h", tool_use_id="t", hook_event="PermissionRequest")
    assert perm.permission_request_result == req
    retry = hook_result_from_json(
        HOOK, {"hookSpecificOutput": {"hookEventName": "PermissionDenied", "retry": True}},
        command="c", hook_name="h", tool_use_id="t", hook_event="PermissionDenied")
    assert retry.retry is True
    blocked = hook_result_from_json(HOOK, {"decision": "block", "reason": "x"}, command="c", hook_name="h",
                                    tool_use_id="t", hook_event="Stop")
    assert blocked.blocking_error == HookBlockingError(blocking_error="x", command="c")


def test_result_defaults():
    r = HookResult(hook=HOOK, outcome="success")
    assert (r.attachment, r.system_message, r.blocking_error, r.prevent_continuation, r.stop_reason,
            r.permission_behavior, r.updated_input, r.watch_paths, r.retry) == (
        None, None, None, False, None, None, None, (), None)
    a = AggregatedHookResult()
    assert (a.prevent_continuation, a.additional_contexts, a.watch_paths, a.permission_behavior) == (
        False, (), (), None)


def test_blocking_messages():
    err = HookBlockingError(blocking_error="no", command="c")
    assert get_pre_tool_hook_blocking_message("PreToolUse:Bash", err) == "PreToolUse:Bash hook error: no"
    assert get_stop_hook_message(err) == "Stop hook feedback:\nno"
    assert get_user_prompt_submit_hook_blocking_message(err) == "UserPromptSubmit operation blocked by hook:\nno"
