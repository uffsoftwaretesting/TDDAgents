"""
Auto mode: an 'ask' is answered by the YOLO classifier instead of a person.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`hasPermissionsToUseTool` (the `mode === 'auto'` branch and the denial reset on allow),
`persistDenialState`, `handleDenialLimitExceeded`.

The branch runs only on an 'ask' that `dontAsk` did not already deny, and **before** the
headless deny — upstream checks it first precisely so a classifier works where no prompt can
be shown. In order:

1. A safety check the classifier may not approve (`classifierApprovable: false`) stays an
   ask — a headless run denies it outright.
2. A tool that requires user interaction keeps its ask.
3. **acceptEdits fast path:** if the tool's own `check_permissions` would allow the call in
   `acceptEdits`, it is allowed without a classifier call.
4. **Allowlisted tools** (`classifier_decision.py`) are allowed without a classifier call.
5. Otherwise the classifier judges the action against the transcript. A block records a
   denial; after `DENIAL_LIMITS` a headless run aborts. An unavailable classifier fails
   **closed** (upstream's `tengu_iron_gate_closed` default). A transcript too long for the
   classifier aborts a headless run and falls back to the ask otherwise.

**TDD:** the phase tier runs inside the inner gate, before any of this. A phase-forbidden
write is already a deny when this branch would see it, so no classifier verdict — and no
fast path — can open one.

A headless run that hits the denial limits, or whose transcript the classifier cannot read,
raises `AutoModeAbortError` — upstream's `AbortError`. As upstream, `run_tool_use`'s outer
handler turns it into the call's `<tool_use_error>`, and the loop continues (the stop hook
still guards termination). Not ported: the PowerShell
guard (no PowerShell tool), `plan` mode with auto active (`autoModeState`; a headless run
never transitions modes), the ant-only error-dump notification, analytics.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.loop.permissions.classifier_decision import is_auto_mode_allowlisted_tool
from app.loop.permissions.denial_tracking import (
    MAX_TOTAL_DENIALS,
    DenialTrackingState,
    create_denial_tracking_state,
    record_denial,
    record_success,
    should_fallback_to_prompting,
)
from app.loop.permissions.rules import extract_permission_context, requires_user_interaction
from app.loop.permissions.types import PermissionBehavior, PermissionMode, PermissionResult, ToolPermissionContext

if TYPE_CHECKING:
    from app.loop.context import ToolContext
    from app.loop.tools.base import Tool

logger = logging.getLogger("TDDOrchestrator.Permissions")

AGENT_TOOL_NAME = "Agent"
REPL_TOOL_NAME = "REPL"

SAFETY_CHECK_HEADLESS_REASON = (
    "Safety check requires interactive approval and permission prompts are not available in this context"
)
TRANSCRIPT_TOO_LONG_ABORT = (
    "Agent aborted: auto mode classifier transcript exceeded context window in headless mode"
)
TRANSCRIPT_TOO_LONG_REASON = (
    "Auto mode classifier transcript exceeded context window — falling back to manual approval"
)
DENIAL_LIMIT_ABORT = "Agent aborted: too many classifier denials in headless mode"


def current_denial_state(context: ToolContext) -> DenialTrackingState:
    """`context.localDenialTracking ?? appState.denialTracking ?? createDenialTrackingState()`."""
    local = getattr(context, "local_denial_tracking", None)
    if local is not None:
        local_state: DenialTrackingState = local.state
        return local_state
    state = context.get_app_state().denial_tracking
    return state if state is not None else create_denial_tracking_state()


def persist_denial_state(context: ToolContext, new_state: DenialTrackingState) -> None:
    """`persistDenialState`: the worker's local box, else the app state (unchanged: no write)."""
    local = getattr(context, "local_denial_tracking", None)
    if local is not None:
        local.state = new_state
        return

    def update(prev: Any) -> Any:
        if prev.denial_tracking is new_state:
            return prev
        return replace(prev, denial_tracking=new_state)

    context.set_app_state(update)


def reset_denials_on_allow(context: ToolContext, perm_ctx: ToolPermissionContext) -> None:
    """An allowed call in auto mode — by any route — breaks the consecutive-denial streak."""
    if perm_ctx.mode != PermissionMode.AUTO:
        return
    state = getattr(context, "local_denial_tracking", None)
    current = state.state if state is not None else context.get_app_state().denial_tracking
    if current is not None and current.consecutive_denials > 0:
        persist_denial_state(context, record_success(current))


class AutoModeAbortError(Exception):
    """
    Upstream's `AbortError` thrown from `hasPermissionsToUseTool` in a headless run. Like
    upstream's, it is caught by the tool pipeline's outer handler (`runToolUse`'s `catch`),
    which reports it as the call's `<tool_use_error>`; the turn goes on.
    """


def _with_accept_edits(context: ToolContext) -> ToolContext:
    """The context a tool sees for the acceptEdits fast path: same state, mode `acceptEdits`."""
    perm_ctx = extract_permission_context(context)
    accept = replace(perm_ctx, mode=PermissionMode.ACCEPT_EDITS)
    get_state = context.get_app_state

    def get_app_state() -> Any:
        return replace(get_state(), tool_permission_context=accept)

    permission_context = accept if getattr(context, "permission_context", None) is not None else None
    return replace(context, get_app_state=get_app_state, permission_context=permission_context)


def handle_denial_limit_exceeded(
    denial_state: DenialTrackingState,
    perm_ctx: ToolPermissionContext,
    classifier_reason: str,
    result: PermissionResult,
    context: ToolContext,
) -> PermissionResult | None:
    """`handleDenialLimitExceeded`: `None` below the limits."""
    if not should_fallback_to_prompting(denial_state):
        return None
    hit_total = denial_state.total_denials >= MAX_TOTAL_DENIALS
    if hit_total:
        warning = (
            f"{denial_state.total_denials} actions were blocked this session. "
            "Please review the transcript before continuing."
        )
    else:
        warning = (
            f"{denial_state.consecutive_denials} consecutive actions were blocked. "
            "Please review the transcript before continuing."
        )
    if perm_ctx.should_avoid_permission_prompts:
        raise AutoModeAbortError(DENIAL_LIMIT_ABORT)
    logger.warning("Classifier denial limit exceeded, falling back to prompting: %s", warning)
    if hit_total:
        persist_denial_state(context, replace(denial_state, total_denials=0, consecutive_denials=0))
    reason = result.decision_reason or {}
    original = reason.get("classifier") if reason.get("type") == "classifier" else "auto-mode"
    return replace(
        result,
        decision_reason={
            "type": "classifier",
            "classifier": original,
            "reason": f"{warning}\n\nLatest blocked action: {classifier_reason}",
        },
    )


async def auto_mode_decision(
    result: PermissionResult,
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    perm_ctx: ToolPermissionContext,
) -> PermissionResult:
    """The `mode === 'auto'` branch of `hasPermissionsToUseTool`, for an 'ask'."""
    from app.loop.permissions.yolo_classifier import (
        build_classifier_unavailable_message,
        build_yolo_rejection_message,
        cached_tddagents_md,
        classify_yolo_action,
        format_action_for_classifier,
    )

    reason = result.decision_reason or {}
    if reason.get("type") == "safetyCheck" and not reason.get("classifierApprovable"):
        if perm_ctx.should_avoid_permission_prompts:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=result.message,
                decision_reason={"type": "asyncAgent", "reason": SAFETY_CHECK_HEADLESS_REASON},
            )
        return result
    if requires_user_interaction(tool, input_args):
        return result

    denial_state = current_denial_state(context)

    if tool.name not in (AGENT_TOOL_NAME, REPL_TOOL_NAME):
        try:
            pending = tool.check_permissions(input_args, _with_accept_edits(context))
            fast = await pending if inspect.isawaitable(pending) else pending
        except Exception:  # noqa: BLE001 - a failing fast path falls through to the classifier
            fast = None
        if fast is not None and fast.behavior == PermissionBehavior.ALLOW:
            persist_denial_state(context, record_success(denial_state))
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                updated_input=fast.updated_input if fast.updated_input is not None else input_args,
                decision_reason={"type": "mode", "mode": PermissionMode.AUTO.value},
            )

    if is_auto_mode_allowlisted_tool(tool.name):
        persist_denial_state(context, record_success(denial_state))
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "mode", "mode": PermissionMode.AUTO.value},
        )

    classify = getattr(context, "auto_mode_classifier", None) or classify_yolo_action
    verdict = await classify(
        tuple(getattr(context, "messages", ()) or ()),
        format_action_for_classifier(tool.name, input_args),
        tuple(getattr(context, "tools", ()) or ()),
        perm_ctx,
        context.cancel,
        tddagents_md=cached_tddagents_md(str(getattr(context, "run_id", "") or "")),
    )

    if verdict.should_block:
        if verdict.transcript_too_long:
            if perm_ctx.should_avoid_permission_prompts:
                raise AutoModeAbortError(TRANSCRIPT_TOO_LONG_ABORT)
            logger.warning("Auto mode classifier transcript too long, falling back to normal permission handling")
            return replace(result, decision_reason={"type": "other", "reason": TRANSCRIPT_TOO_LONG_REASON})
        if verdict.unavailable:
            logger.warning("Auto mode classifier unavailable, denying with retry guidance (fail closed)")
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=build_classifier_unavailable_message(tool.name, verdict.model),
                decision_reason={"type": "classifier", "classifier": "auto-mode", "reason": "Classifier unavailable"},
            )
        new_state = record_denial(denial_state)
        persist_denial_state(context, new_state)
        logger.warning("Auto mode classifier blocked action: %s", verdict.reason)
        limited = handle_denial_limit_exceeded(new_state, perm_ctx, verdict.reason, result, context)
        if limited is not None:
            return limited
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=build_yolo_rejection_message(verdict.reason),
            decision_reason={"type": "classifier", "classifier": "auto-mode", "reason": verdict.reason},
        )

    persist_denial_state(context, record_success(denial_state))
    return PermissionResult(
        behavior=PermissionBehavior.ALLOW,
        updated_input=input_args,
        decision_reason={"type": "classifier", "classifier": "auto-mode", "reason": verdict.reason},
    )


__all__ = [
    "DENIAL_LIMIT_ABORT",
    "AutoModeAbortError",
    "SAFETY_CHECK_HEADLESS_REASON",
    "TRANSCRIPT_TOO_LONG_ABORT",
    "TRANSCRIPT_TOO_LONG_REASON",
    "auto_mode_decision",
    "current_denial_state",
    "handle_denial_limit_exceeded",
    "persist_denial_state",
    "reset_denials_on_allow",
]
