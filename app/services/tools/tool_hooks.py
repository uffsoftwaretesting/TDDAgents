"""
The hooks around one tool call.

Ported from `reference/claude-code/src/services/tools/toolHooks.ts` -> `runPreToolUseHooks`,
`runPostToolUseHooks`, `runPostToolUseFailureHooks`, and
`src/utils/permissions/PermissionResult.ts` -> `getRuleBehaviorDescription`.

Each runner turns `executeHooks`' aggregated stream into what the tool pipeline acts on:

* **PreToolUse** — a permission verdict (a blocking error is a deny; otherwise the
  aggregated `permissionDecision`, deny > ask > allow), a rewritten input when a hook
  rewrote without deciding, `continue: false` (the call still resolves, then the run stops
  with `hook_stopped`), and additional context. A cancelled run stops the call.
* **PostToolUse / PostToolUseFailure** — additive only: blocking errors and additional
  context become messages after the tool result, and `continue: false` stops the run. They
  cannot un-run the tool.

A hook's verdict is only an opinion here; `services/tools/tool_hooks.py` re-checks it against
the permission rules and the TDD phase tier, so no hook can approve a phase-forbidden write.

Resolving a PreToolUse hook's verdict against the permission rules.

Ported from `reference/claude-code/src/services/tools/toolHooks.ts` ->
`resolveHookPermissionDecision`, whose own docstring states the invariant it exists for:

    Encapsulates the invariant that hook 'allow' does NOT bypass settings.json
    deny/ask rules — checkRuleBasedPermissions still applies (inc-4788 analog).

* **allow** — re-checked with `check_rule_based_permissions` only (the TDD managed tier,
  deny and ask rules, the tool's deny, content ask rules, safety checks). No objection: the
  hook's allow stands, so it *does* skip an 'ask' no rule produced (a passthrough, a
  default-mode write) — in headless and dontAsk runs too, exactly as upstream. A rule deny
  wins; a rule ask goes to the full gate. A tool that requires interaction goes to the full
  gate unless the hook supplied `updatedInput` (the hook *was* the interaction).
* **deny** — final, on the original input.
* **ask** — upstream's `forceDecision`, which skips `hasPermissionsToUseTool` entirely.
  *Divergence (TDD, and v2.1.101's "ask no longer overrides deny" fix):* the TDD tier and
  deny rules run first; only then is the hook's ask handled as an ask — denied under
  dontAsk, offered to PermissionRequest hooks when no prompt can be shown, else returned.
* **no verdict** — the full gate.

The verdict comes from `app/services/tools/tool_hooks.py::run_pre_tool_use_hooks`.

Not ported: upstream's `requireCanUseTool` context flag (speculative overlays), which this
loop has no use for.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.types.hooks import get_pre_tool_hook_blocking_message
from app.types.permissions import PermissionBehavior, PermissionResult
from app.utils.hooks.hooks import (
    execute_post_tool_hooks,
    execute_post_tool_use_failure_hooks,
    execute_pre_tool_hooks,
)
from app.utils.messages import HookAttachment
from app.utils.permissions.permissions import (
    check_rule_based_permissions,
    create_permission_request_message,
    has_permissions_to_use_tool,
    requires_user_interaction,
    resolve_ask_without_prompt,
)
from app.utils.shell_command import is_aborted

logger = logging.getLogger("TDDOrchestrator.Hooks")


PERMISSION_DENIED_RETRY_MESSAGE = (
    "The PermissionDenied hook indicated this command is now approved. You may retry it if you would like."
)


async def run_permission_denied_hooks(
    context: Any, tool_name: str, tool_input: Mapping[str, Any], tool_use_id: str, reason: str
) -> bool:
    """
    The PermissionDenied block of `checkPermissionsAndCallTool`: after an auto-mode classifier
    denial, whether any hook said `retry`.
    """
    from app.utils.hooks.hooks import execute_permission_denied_hooks

    retry = False
    async for result in execute_permission_denied_hooks(
        context, tool_name, tool_use_id, tool_input, reason, _permission_mode(context), getattr(context, "cancel", None)
    ):
        if result.retry:
            retry = True
    return retry


def get_rule_behavior_description(behavior: str) -> str:
    """`getRuleBehaviorDescription`."""
    if behavior == "allow":
        return "allowed"
    if behavior == "deny":
        return "denied"
    return "asked for confirmation for"


def _permission_mode(context: Any) -> str | None:
    get_state = getattr(context, "get_app_state", None)
    if get_state is None:
        return None
    try:
        return str(get_state().tool_permission_context.mode)
    except Exception:  # noqa: BLE001 - a context without permissions reports no mode
        return None


@dataclass
class PreToolHooksOutcome:
    """Everything `runPreToolUseHooks` yielded, folded the way `toolExecution.ts` folds it."""

    attachments: list[HookAttachment] = field(default_factory=list)
    verdict: HookPermissionVerdict | None = None
    updated_input: dict[str, Any] | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    stopped: bool = False


async def run_pre_tool_use_hooks(
    context: Any, tool_name: str, tool_input: Mapping[str, Any], tool_use_id: str
) -> PreToolHooksOutcome:
    """`runPreToolUseHooks`, consumed in place (the `switch` in `checkPermissionsAndCallTool`)."""
    # Imported here: `app.tools.tools` imports the tool pipeline, which imports this module.

    outcome = PreToolHooksOutcome()
    hook_name = f"PreToolUse:{tool_name}"
    cancel = getattr(context, "cancel", None)
    try:
        async for result in execute_pre_tool_hooks(
            context, tool_name, tool_use_id, tool_input, _permission_mode(context), cancel
        ):
            if result.attachment is not None:
                outcome.attachments.append(result.attachment)
            if result.blocking_error is not None:
                outcome.verdict = HookPermissionVerdict(
                    behavior=PermissionBehavior.DENY,
                    message=get_pre_tool_hook_blocking_message(hook_name, result.blocking_error),
                )
            if result.prevent_continuation:
                outcome.prevent_continuation = True
                if result.stop_reason:
                    outcome.stop_reason = result.stop_reason
            if result.permission_behavior is not None:
                behavior = PermissionBehavior(result.permission_behavior)
                if behavior == PermissionBehavior.ALLOW:
                    updated = dict(result.updated_input) if result.updated_input is not None else None
                    outcome.verdict = HookPermissionVerdict(behavior=behavior, updated_input=updated)
                else:
                    message = result.hook_permission_decision_reason or (
                        f"Hook {hook_name} {get_rule_behavior_description(behavior.value)} this tool"
                    )
                    updated = (
                        dict(result.updated_input)
                        if behavior == PermissionBehavior.ASK and result.updated_input is not None
                        else None
                    )
                    outcome.verdict = HookPermissionVerdict(behavior=behavior, message=message, updated_input=updated)
            if result.updated_input is not None and result.permission_behavior is None:
                outcome.updated_input = dict(result.updated_input)
            if result.additional_contexts:
                outcome.attachments.append(
                    HookAttachment(
                        type="hook_additional_context",
                        content=tuple(result.additional_contexts),
                        hook_name=hook_name,
                        tool_use_id=tool_use_id,
                        hook_event="PreToolUse",
                    )
                )
            if is_aborted(cancel):
                outcome.attachments.append(
                    HookAttachment(
                        type="hook_cancelled", hook_name=hook_name, tool_use_id=tool_use_id, hook_event="PreToolUse"
                    )
                )
                outcome.stopped = True
                return outcome
    except Exception:
        logger.exception("PreToolUse hooks failed for %s", tool_name)
        outcome.attachments.append(
            HookAttachment(
                type="hook_error_during_execution",
                content="PreToolUse hooks failed",
                hook_name=hook_name,
                tool_use_id=tool_use_id,
                hook_event="PreToolUse",
            )
        )
        outcome.stopped = True
    return outcome


@dataclass
class PostToolHooksOutcome:
    """What `runPostToolUseHooks` / `runPostToolUseFailureHooks` yielded."""

    attachments: list[HookAttachment] = field(default_factory=list)
    prevent_continuation: bool = False
    updated_mcp_tool_output: Any = None


async def run_post_tool_use_hooks(
    context: Any,
    tool_name: str,
    tool_input: Mapping[str, Any],
    tool_response: Any,
    tool_use_id: str,
    is_mcp: bool = False,
) -> PostToolHooksOutcome:
    """`runPostToolUseHooks`."""
    return await _run_post(
        "PostToolUse",
        tool_name,
        tool_use_id,
        execute_post_tool_hooks(
            context, tool_name, tool_use_id, tool_input, tool_response, _permission_mode(context),
            getattr(context, "cancel", None),
        ),
        is_mcp=is_mcp,
    )


async def run_post_tool_use_failure_hooks(
    context: Any,
    tool_name: str,
    tool_input: Mapping[str, Any],
    error: str,
    tool_use_id: str,
    is_interrupt: bool | None = None,
) -> PostToolHooksOutcome:
    """`runPostToolUseFailureHooks`."""
    return await _run_post(
        "PostToolUseFailure",
        tool_name,
        tool_use_id,
        execute_post_tool_use_failure_hooks(
            context, tool_name, tool_use_id, tool_input, error, is_interrupt, _permission_mode(context),
            getattr(context, "cancel", None),
        ),
        is_mcp=False,
    )


async def _run_post(
    event: str, tool_name: str, tool_use_id: str, stream: Any, *, is_mcp: bool
) -> PostToolHooksOutcome:
    outcome = PostToolHooksOutcome()
    hook_name = f"{event}:{tool_name}"
    try:
        async for result in stream:
            try:
                attachment = result.attachment
                if attachment is not None and attachment.type == "hook_cancelled":
                    outcome.attachments.append(
                        HookAttachment(
                            type="hook_cancelled", hook_name=hook_name, tool_use_id=tool_use_id, hook_event=event
                        )
                    )
                    continue
                # A JSON `block` yields both a blocking error and its attachment; the
                # blocking-error branch below builds the same attachment (#31301).
                if attachment is not None and attachment.type != "hook_blocking_error":
                    outcome.attachments.append(attachment)
                if result.blocking_error is not None:
                    outcome.attachments.append(
                        HookAttachment(
                            type="hook_blocking_error",
                            hook_name=hook_name,
                            tool_use_id=tool_use_id,
                            hook_event=event,
                            blocking_error=result.blocking_error,
                        )
                    )
                if event == "PostToolUse" and result.prevent_continuation:
                    outcome.attachments.append(
                        HookAttachment(
                            type="hook_stopped_continuation",
                            message=result.stop_reason or "Execution stopped by PostToolUse hook",
                            hook_name=hook_name,
                            tool_use_id=tool_use_id,
                            hook_event=event,
                        )
                    )
                    outcome.prevent_continuation = True
                    return outcome
                if result.additional_contexts:
                    outcome.attachments.append(
                        HookAttachment(
                            type="hook_additional_context",
                            content=tuple(result.additional_contexts),
                            hook_name=hook_name,
                            tool_use_id=tool_use_id,
                            hook_event=event,
                        )
                    )
                if result.updated_mcp_tool_output is not None and is_mcp:
                    outcome.updated_mcp_tool_output = result.updated_mcp_tool_output
            except Exception as exc:  # noqa: BLE001 - one bad result must not drop the rest
                outcome.attachments.append(
                    HookAttachment(
                        type="hook_error_during_execution",
                        content=str(exc),
                        hook_name=hook_name,
                        tool_use_id=tool_use_id,
                        hook_event=event,
                    )
                )
    except Exception:
        logger.exception("%s hooks failed for %s", event, tool_name)
    return outcome


if TYPE_CHECKING:
    from app.tool import Tool, ToolContext


@dataclass(frozen=True, slots=True)
class HookPermissionVerdict:
    """A PreToolUse hook's permission opinion, as the dispatcher reports it."""

    behavior: PermissionBehavior | None = None
    message: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPermission:
    """`resolveHookPermissionDecision`'s `{decision, input}`."""

    decision: PermissionResult
    input: dict[str, Any]


async def resolve_hook_permission_decision(
    hook_verdict: HookPermissionVerdict | None,
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> ResolvedPermission:
    """`resolveHookPermissionDecision`: the decision and the input the tool will run on."""
    behavior = hook_verdict.behavior if hook_verdict is not None else None
    hook_name = f"PreToolUse:{tool.name}"

    if hook_verdict is not None and behavior == PermissionBehavior.ALLOW:
        hook_input = hook_verdict.updated_input if hook_verdict.updated_input is not None else input_args
        interaction_satisfied = hook_verdict.updated_input is not None
        if requires_user_interaction(tool, hook_input) and not interaction_satisfied:
            decision = await has_permissions_to_use_tool(tool, hook_input, context, tool_use_id)
            return ResolvedPermission(decision=decision, input=hook_input)
        rule_check = await check_rule_based_permissions(tool, hook_input, context)
        if rule_check is None:
            # No rule objects: the hook's approval stands and the prompt is skipped.
            return ResolvedPermission(
                decision=PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    updated_input=hook_input,
                    decision_reason={"type": "hook", "hookName": hook_name},
                ),
                input=hook_input,
            )
        if rule_check.behavior == PermissionBehavior.DENY:
            return ResolvedPermission(decision=rule_check, input=hook_input)
        # An ask rule: the prompt is required despite the hook's approval.
        decision = await has_permissions_to_use_tool(tool, hook_input, context, tool_use_id)
        return ResolvedPermission(decision=decision, input=hook_input)

    if hook_verdict is not None and behavior == PermissionBehavior.DENY:
        return ResolvedPermission(
            decision=PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=hook_verdict.message or f"Permission to use {tool.name} has been denied.",
                decision_reason={"type": "hook", "hookName": hook_name},
            ),
            input=input_args,
        )

    if hook_verdict is not None and behavior == PermissionBehavior.ASK:
        ask_input = hook_verdict.updated_input if hook_verdict.updated_input is not None else input_args
        rule_check = await check_rule_based_permissions(tool, ask_input, context)
        if rule_check is not None and rule_check.behavior == PermissionBehavior.DENY:
            return ResolvedPermission(decision=rule_check, input=ask_input)
        forced = PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=hook_verdict.message or create_permission_request_message(
                tool.name, {"type": "hook", "hookName": hook_name}),
            decision_reason={"type": "hook", "hookName": hook_name, "reason": hook_verdict.message},
        )
        decision = await resolve_ask_without_prompt(forced, tool, ask_input, context, tool_use_id)
        return ResolvedPermission(decision=decision, input=ask_input)

    return ResolvedPermission(
        decision=await has_permissions_to_use_tool(tool, input_args, context, tool_use_id),
        input=input_args,
    )


__all__ = [
    "PERMISSION_DENIED_RETRY_MESSAGE",
    "HookPermissionVerdict",
    "PostToolHooksOutcome",
    "PreToolHooksOutcome",
    "ResolvedPermission",
    "get_rule_behavior_description",
    "resolve_hook_permission_decision",
    "run_permission_denied_hooks",
    "run_post_tool_use_failure_hooks",
    "run_post_tool_use_hooks",
    "run_pre_tool_use_hooks",
]
