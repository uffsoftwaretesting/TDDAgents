"""
What a hook said: parsing its output and turning it into a result.

Ported from:
- `reference/claude-code/src/types/hooks.ts` -> `syncHookResponseSchema`,
  `hookJSONOutputSchema`, `isAsyncHookJSONOutput`, `HookResult`, `PermissionRequestResult`
- `reference/claude-code/src/utils/hooks.ts` -> `validateHookJson`, `parseHookOutput`,
  `parseHttpHookOutput`, `processHookJSONOutput`, `getPreToolHookBlockingMessage`,
  `getStopHookMessage`, `getUserPromptSubmitHookBlockingMessage`

The JSON contract is upstream's: `continue: false` (+ `stopReason`) stops the run; the legacy
top-level `decision` (`approve`/`block`) maps to allow/deny; `hookSpecificOutput` carries the
per-event fields (`permissionDecision`, `updatedInput`, `additionalContext`, the
PermissionRequest `decision`, ...). A document that does not validate is a non-blocking error
whose message carries the schema hint, exactly as upstream prints it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping

from app.loop.hooks.messages import HookAttachment, HookBlockingError
from app.loop.hooks.schemas import HookCommand
from app.loop.permissions.updates import parse_permission_updates

HookOutcomeKind = Literal["success", "blocking", "non_blocking_error", "cancelled"]
PermissionBehaviorName = Literal["allow", "deny", "ask", "passthrough"]

_PERMISSION_BEHAVIORS = ("allow", "deny", "ask")

#: `parseHookOutput`'s schema hint, printed after a validation failure.
EXPECTED_SCHEMA_HINT: dict[str, Any] = {
    "continue": "boolean (optional)",
    "suppressOutput": "boolean (optional)",
    "stopReason": "string (optional)",
    "decision": '"approve" | "block" (optional)',
    "reason": "string (optional)",
    "systemMessage": "string (optional)",
    "permissionDecision": '"allow" | "deny" | "ask" (optional)',
    "hookSpecificOutput": {
        "for PreToolUse": {
            "hookEventName": '"PreToolUse"',
            "permissionDecision": '"allow" | "deny" | "ask" (optional)',
            "permissionDecisionReason": "string (optional)",
            "updatedInput": "object (optional) - Modified tool input to use",
        },
        "for UserPromptSubmit": {
            "hookEventName": '"UserPromptSubmit"',
            "additionalContext": "string (required)",
        },
        "for PostToolUse": {
            "hookEventName": '"PostToolUse"',
            "additionalContext": "string (optional)",
        },
    },
}

#: `hookSpecificOutput` fields per `hookEventName`, with the type each must have.
_SPECIFIC_FIELDS: dict[str, dict[str, str]] = {
    "PreToolUse": {
        "permissionDecision": "behavior",
        "permissionDecisionReason": "string",
        "updatedInput": "record",
        "additionalContext": "string",
    },
    "UserPromptSubmit": {"additionalContext": "string"},
    "SessionStart": {"additionalContext": "string", "initialUserMessage": "string", "watchPaths": "strings"},
    "Setup": {"additionalContext": "string"},
    "SubagentStart": {"additionalContext": "string"},
    "PostToolUse": {"additionalContext": "string", "updatedMCPToolOutput": "unknown"},
    "PostToolUseFailure": {"additionalContext": "string"},
    "PermissionDenied": {"retry": "boolean"},
    "Notification": {"additionalContext": "string"},
    "PermissionRequest": {"decision": "decision"},
    "Elicitation": {"action": "action", "content": "record"},
    "ElicitationResult": {"action": "action", "content": "record"},
    "CwdChanged": {"watchPaths": "strings"},
    "FileChanged": {"watchPaths": "strings"},
    "WorktreeCreate": {"worktreePath": "string"},
}
_REQUIRED_SPECIFIC: dict[str, tuple[str, ...]] = {
    "PermissionRequest": ("decision",),
    "WorktreeCreate": ("worktreePath",),
}
_TOP_LEVEL: dict[str, str] = {
    "continue": "boolean",
    "suppressOutput": "boolean",
    "stopReason": "string",
    "decision": "decision_legacy",
    "reason": "string",
    "systemMessage": "string",
}


def _type_error(kind: str, value: Any) -> str | None:
    """Why `value` is not a `kind`, or `None` when it is."""
    if kind == "unknown":
        return None
    if kind == "string":
        return None if isinstance(value, str) else "Expected string"
    if kind == "boolean":
        return None if isinstance(value, bool) else "Expected boolean"
    if kind == "record":
        return None if isinstance(value, Mapping) else "Expected object"
    if kind == "strings":
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        return None if ok else "Expected array of strings"
    if kind == "behavior":
        return None if value in _PERMISSION_BEHAVIORS else 'Expected "allow" | "deny" | "ask"'
    if kind == "decision_legacy":
        return None if value in ("approve", "block") else 'Expected "approve" | "block"'
    if kind == "action":
        return None if value in ("accept", "decline", "cancel") else 'Expected "accept" | "decline" | "cancel"'
    if kind == "decision":
        return _permission_request_decision_error(value)
    raise ValueError(kind)  # pragma: no cover - the tables above name only known kinds


def _permission_request_decision_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "Expected object"
    behavior = value.get("behavior")
    if behavior == "allow":
        if "updatedInput" in value and not isinstance(value["updatedInput"], Mapping):
            return "updatedInput: Expected object"
        if "updatedPermissions" in value and parse_permission_updates(value["updatedPermissions"]) is None:
            return "updatedPermissions: Invalid permission update"
        return None
    if behavior == "deny":
        if "message" in value and not isinstance(value["message"], str):
            return "message: Expected string"
        if "interrupt" in value and not isinstance(value["interrupt"], bool):
            return "interrupt: Expected boolean"
        return None
    return 'behavior: Expected "allow" | "deny"'


def is_async_hook_json_output(json_output: Mapping[str, Any]) -> bool:
    """`isAsyncHookJSONOutput`."""
    return json_output.get("async") is True


def _is_valid_async(parsed: Mapping[str, Any]) -> bool:
    """`asyncHookResponseSchema`: `async: true` with an optional numeric `asyncTimeout`."""
    if parsed.get("async") is not True:
        return False
    timeout = parsed.get("asyncTimeout")
    return timeout is None or (isinstance(timeout, (int, float)) and not isinstance(timeout, bool))


def hook_json_errors(parsed: Any) -> list[str]:
    """
    `hookJSONOutputSchema().safeParse` issues, each as `path: message`. Empty when valid.

    Upstream's schema is a union of the async response (`async: true`, optional numeric
    `asyncTimeout`) and the sync response; an object that is neither fails.
    """
    if not isinstance(parsed, Mapping):
        return [": Expected object"]
    if _is_valid_async(parsed):
        return []
    # Not a valid async response: Zod's union then tries the sync schema, which strips the
    # unknown `async`/`asyncTimeout` keys rather than rejecting them.
    errors: list[str] = []
    for key, kind in _TOP_LEVEL.items():
        if key in parsed and parsed[key] is not None:
            err = _type_error(kind, parsed[key])
            if err:
                errors.append(f"{key}: {err}")
    specific = parsed.get("hookSpecificOutput")
    if specific is not None:
        if not isinstance(specific, Mapping):
            errors.append("hookSpecificOutput: Expected object")
        else:
            event = specific.get("hookEventName")
            fields = _SPECIFIC_FIELDS.get(event) if isinstance(event, str) else None
            if fields is None:
                errors.append("hookSpecificOutput.hookEventName: Invalid input")
            else:
                for key in _REQUIRED_SPECIFIC.get(str(event), ()):
                    if key not in specific:
                        errors.append(f"hookSpecificOutput.{key}: Required")
                for key, kind in fields.items():
                    if key in specific and specific[key] is not None:
                        err = _type_error(kind, specific[key])
                        if err:
                            errors.append(f"hookSpecificOutput.{key}: {err}")
    return errors


@dataclass(frozen=True, slots=True)
class ParsedHookOutput:
    """`parseHookOutput`'s `{json?, plainText?, validationError?}`."""

    json: Mapping[str, Any] | None = None
    plain_text: str | None = None
    validation_error: str | None = None


def validate_hook_json(text: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """`validateHookJson`: `(json, None)` when valid, `(None, error)` when not. Raises on bad JSON."""
    parsed = json.loads(text)
    issues = hook_json_errors(parsed)
    if not issues:
        if not _is_valid_async(parsed) and "async" in parsed:
            # Parsed by the sync branch of the union, which drops the async keys.
            parsed = {k: v for k, v in parsed.items() if k not in ("async", "asyncTimeout")}
        return parsed, None
    lines = "\n".join(f"  - {issue}" for issue in issues)
    return None, (
        f"Hook JSON output validation failed:\n{lines}\n\n"
        f"The hook's output was: {json.dumps(parsed, indent=2)}"
    )


def parse_hook_output(stdout: str) -> ParsedHookOutput:
    """`parseHookOutput`: plain text unless the trimmed output starts with `{`."""
    trimmed = stdout.strip()
    if not trimmed.startswith("{"):
        return ParsedHookOutput(plain_text=stdout)
    try:
        parsed, error = validate_hook_json(trimmed)
    except ValueError:
        return ParsedHookOutput(plain_text=stdout)
    if parsed is not None:
        return ParsedHookOutput(json=parsed)
    message = f"{error}\n\nExpected schema:\n{json.dumps(EXPECTED_SCHEMA_HINT, indent=2)}"
    return ParsedHookOutput(plain_text=stdout, validation_error=message)


def parse_http_hook_output(body: str) -> ParsedHookOutput:
    """`parseHttpHookOutput`: an HTTP hook must answer JSON; an empty body is `{}`."""
    trimmed = body.strip()
    if trimmed == "":
        return ParsedHookOutput(json={})
    if not trimmed.startswith("{"):
        shown = trimmed[:200] + "…" if len(trimmed) > 200 else trimmed
        return ParsedHookOutput(validation_error=f"HTTP hook must return JSON, but got non-JSON response body: {shown}")
    try:
        parsed, error = validate_hook_json(trimmed)
    except ValueError as exc:
        return ParsedHookOutput(validation_error=f"HTTP hook must return valid JSON, but parsing failed: {exc}")
    if parsed is not None:
        return ParsedHookOutput(json=parsed)
    return ParsedHookOutput(validation_error=error)


@dataclass(frozen=True, slots=True)
class HookResult:
    """`HookResult`: one hook's outcome."""

    hook: HookCommand
    outcome: HookOutcomeKind
    attachment: HookAttachment | None = None
    system_message: str | None = None
    blocking_error: HookBlockingError | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    permission_behavior: PermissionBehaviorName | None = None
    hook_permission_decision_reason: str | None = None
    additional_context: str | None = None
    initial_user_message: str | None = None
    updated_input: Mapping[str, Any] | None = None
    updated_mcp_tool_output: Any = None
    permission_request_result: Mapping[str, Any] | None = None
    watch_paths: tuple[str, ...] = ()
    retry: bool | None = None


@dataclass(frozen=True, slots=True)
class AggregatedHookResult:
    """
    `AggregatedHookResult`: one item of `executeHooks`' stream. Each item carries one kind
    of news, the way upstream yields them; the consumer reads the fields it cares about.
    """

    attachment: HookAttachment | None = None
    blocking_error: HookBlockingError | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    hook_permission_decision_reason: str | None = None
    hook_source: str | None = None
    permission_behavior: str | None = None
    additional_contexts: tuple[str, ...] = ()
    initial_user_message: str | None = None
    updated_input: Mapping[str, Any] | None = None
    updated_mcp_tool_output: Any = None
    permission_request_result: Mapping[str, Any] | None = None
    watch_paths: tuple[str, ...] = ()
    retry: bool | None = None


@dataclass(frozen=True, slots=True)
class _Partial:
    """The fields `processHookJSONOutput` fills, before the outcome is known."""

    prevent_continuation: bool = False
    stop_reason: str | None = None
    permission_behavior: PermissionBehaviorName | None = None
    blocking_error: HookBlockingError | None = None
    system_message: str | None = None
    hook_permission_decision_reason: str | None = None
    additional_context: str | None = None
    initial_user_message: str | None = None
    updated_input: Mapping[str, Any] | None = None
    updated_mcp_tool_output: Any = None
    permission_request_result: Mapping[str, Any] | None = None
    watch_paths: tuple[str, ...] = ()
    retry: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class HookOutputError(ValueError):
    """A hook output that `processHookJSONOutput` throws on."""


def process_hook_json_output(
    json_output: Mapping[str, Any],
    *,
    command: str,
    hook_name: str,
    tool_use_id: str,
    hook_event: str,
    expected_hook_event: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
) -> tuple[_Partial, HookAttachment]:
    """`processHookJSONOutput`: the fields the JSON sets, plus the attachment to report."""
    result = _Partial()

    def block(reason: str) -> HookBlockingError:
        return HookBlockingError(blocking_error=reason, command=command)

    if json_output.get("continue") is False:
        result = replace(result, prevent_continuation=True)
        if json_output.get("stopReason"):
            result = replace(result, stop_reason=str(json_output["stopReason"]))

    reason = json_output.get("reason")
    decision = json_output.get("decision")
    if decision:
        if decision == "approve":
            result = replace(result, permission_behavior="allow")
        elif decision == "block":
            result = replace(
                result, permission_behavior="deny", blocking_error=block(reason or "Blocked by hook")
            )
        else:
            raise HookOutputError(f"Unknown hook decision type: {decision}. Valid types are: approve, block")

    if json_output.get("systemMessage"):
        result = replace(result, system_message=str(json_output["systemMessage"]))

    specific = json_output.get("hookSpecificOutput")
    specific = specific if isinstance(specific, Mapping) else None
    if specific is not None and specific.get("hookEventName") == "PreToolUse" and specific.get("permissionDecision"):
        pd = specific["permissionDecision"]
        if pd == "allow":
            result = replace(result, permission_behavior="allow")
        elif pd == "deny":
            result = replace(result, permission_behavior="deny", blocking_error=block(reason or "Blocked by hook"))
        elif pd == "ask":
            result = replace(result, permission_behavior="ask")
        else:
            raise HookOutputError(
                f"Unknown hook permissionDecision type: {pd}. Valid types are: allow, deny, ask"
            )
    if result.permission_behavior is not None and reason is not None:
        result = replace(result, hook_permission_decision_reason=str(reason))

    if specific is not None:
        event_name = specific.get("hookEventName")
        if expected_hook_event and event_name != expected_hook_event:
            raise HookOutputError(
                f"Hook returned incorrect event name: expected '{expected_hook_event}' but got "
                f"'{event_name}'. Full stdout: {json.dumps(json_output, indent=2)}"
            )
        context = specific.get("additionalContext")
        if event_name == "PreToolUse":
            pd = specific.get("permissionDecision")
            if pd == "deny":
                text = specific.get("permissionDecisionReason") or reason or "Blocked by hook"
                result = replace(result, blocking_error=block(text))
            result = replace(
                result,
                hook_permission_decision_reason=specific.get("permissionDecisionReason"),
                additional_context=context,
            )
            if specific.get("updatedInput"):
                result = replace(result, updated_input=specific["updatedInput"])
        elif event_name in ("UserPromptSubmit", "Setup", "SubagentStart", "PostToolUseFailure"):
            result = replace(result, additional_context=context)
        elif event_name == "SessionStart":
            result = replace(
                result,
                additional_context=context,
                initial_user_message=specific.get("initialUserMessage"),
                watch_paths=tuple(specific.get("watchPaths") or ()),
            )
        elif event_name == "PostToolUse":
            result = replace(result, additional_context=context)
            if specific.get("updatedMCPToolOutput"):
                result = replace(result, updated_mcp_tool_output=specific["updatedMCPToolOutput"])
        elif event_name == "PermissionDenied":
            result = replace(result, retry=specific.get("retry"))
        elif event_name == "PermissionRequest":
            request = specific.get("decision")
            if request:
                behavior: PermissionBehaviorName = "allow" if request.get("behavior") == "allow" else "deny"
                result = replace(result, permission_request_result=request, permission_behavior=behavior)
                if request.get("behavior") == "allow" and request.get("updatedInput"):
                    result = replace(result, updated_input=request["updatedInput"])
        elif event_name in ("Elicitation", "ElicitationResult") and specific.get("action") == "decline":
            default = "Elicitation denied by hook" if event_name == "Elicitation" else (
                "Elicitation result blocked by hook"
            )
            result = replace(result, blocking_error=block(reason or default))

    if result.blocking_error is not None:
        attachment = HookAttachment(
            type="hook_blocking_error",
            hook_name=hook_name,
            tool_use_id=tool_use_id,
            hook_event=hook_event,
            blocking_error=result.blocking_error,
        )
    else:
        # Empty content: JSON hooks speak through `additionalContext`, never through this
        # field, which suppresses upstream's trivial "X hook success: Success" reminder.
        attachment = HookAttachment(
            type="hook_success",
            hook_name=hook_name,
            tool_use_id=tool_use_id,
            hook_event=hook_event,
            content="",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            command=command,
            duration_ms=duration_ms,
        )
    return result, attachment


def hook_result_from_json(
    hook: HookCommand,
    json_output: Mapping[str, Any],
    **kwargs: Any,
) -> HookResult:
    """`{...processHookJSONOutput(...), outcome: 'success', hook}`."""
    partial, attachment = process_hook_json_output(json_output, **kwargs)
    return HookResult(
        hook=hook,
        outcome="success",
        attachment=attachment,
        system_message=partial.system_message,
        blocking_error=partial.blocking_error,
        prevent_continuation=partial.prevent_continuation,
        stop_reason=partial.stop_reason,
        permission_behavior=partial.permission_behavior,
        hook_permission_decision_reason=partial.hook_permission_decision_reason,
        additional_context=partial.additional_context,
        initial_user_message=partial.initial_user_message,
        updated_input=partial.updated_input,
        updated_mcp_tool_output=partial.updated_mcp_tool_output,
        permission_request_result=partial.permission_request_result,
        watch_paths=partial.watch_paths,
        retry=partial.retry,
    )


def get_pre_tool_hook_blocking_message(hook_name: str, blocking_error: HookBlockingError) -> str:
    """`getPreToolHookBlockingMessage`."""
    return f"{hook_name} hook error: {blocking_error.blocking_error}"


def get_stop_hook_message(blocking_error: HookBlockingError) -> str:
    """`getStopHookMessage`."""
    return f"Stop hook feedback:\n{blocking_error.blocking_error}"


def get_user_prompt_submit_hook_blocking_message(blocking_error: HookBlockingError) -> str:
    """`getUserPromptSubmitHookBlockingMessage`."""
    return f"UserPromptSubmit operation blocked by hook:\n{blocking_error.blocking_error}"


__all__ = [
    "EXPECTED_SCHEMA_HINT",
    "AggregatedHookResult",
    "HookOutputError",
    "HookResult",
    "ParsedHookOutput",
    "get_pre_tool_hook_blocking_message",
    "get_stop_hook_message",
    "get_user_prompt_submit_hook_blocking_message",
    "hook_json_errors",
    "hook_result_from_json",
    "is_async_hook_json_output",
    "parse_hook_output",
    "parse_http_hook_output",
    "process_hook_json_output",
    "validate_hook_json",
]
