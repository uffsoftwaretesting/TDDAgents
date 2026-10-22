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

A hook's verdict is only an opinion here; `tools/hook_permissions.py` re-checks it against
the permission rules and the TDD phase tier, so no hook can approve a phase-forbidden write.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.loop.hooks.execute import (
    execute_post_tool_hooks,
    execute_post_tool_use_failure_hooks,
    execute_pre_tool_hooks,
)
from app.loop.hooks.messages import HookAttachment
from app.loop.hooks.output import get_pre_tool_hook_blocking_message
from app.loop.hooks.process import is_aborted
from app.loop.permissions.types import PermissionBehavior

if TYPE_CHECKING:
    from app.loop.tools.hook_permissions import HookPermissionVerdict

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
    from app.loop.hooks.execute import execute_permission_denied_hooks

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
    # Imported here: `app.loop.tools` imports the tool pipeline, which imports this module.
    from app.loop.tools.hook_permissions import HookPermissionVerdict

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


__all__ = [
    "PERMISSION_DENIED_RETRY_MESSAGE",
    "PostToolHooksOutcome",
    "PreToolHooksOutcome",
    "get_rule_behavior_description",
    "run_permission_denied_hooks",
    "run_post_tool_use_failure_hooks",
    "run_post_tool_use_hooks",
    "run_pre_tool_use_hooks",
]
