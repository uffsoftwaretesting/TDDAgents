"""
The full runtime permission gate (Part C3), with the TDD phase rules as its managed tier.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`hasPermissionsToUseTool` (outer) and `hasPermissionsToUseToolInner` (inner), step for step.

**The TDD managed tier sits above step 1a.** `check_tdd_phase_permission` runs before any
rule, mode or hook, keyed on the ledger rather than on settings, so nothing loaded from a
settings file — not a project `allow`, not `bypassPermissions` — can reach past it. That is
the equivalent of upstream's managed-rules-only option, built into code. It **fails
closed**: if the check itself raises, the call is denied.

Upstream steps not ported, each because the mechanism is absent here: the sandbox
auto-allow exception at 1b, the auto-mode classifier and denial tracking in the outer gate,
and the PowerShell guard.

Rule-based permission evaluation (Part C2).

Ported from:
- `reference/claude-code/src/utils/permissions/permissions.ts`
  -> `checkRuleBasedPermissions`, `getDenyRuleForTool`, `getAskRuleForTool`

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

PermissionRequest hooks for a context that cannot show a prompt.

Ported from `reference/claude-code/src/utils/permissions/permissions.ts` ->
`runPermissionRequestHooksForHeadlessAgent`, and the decision shape of
`src/types/hooks.ts` (`PermissionRequest` `decision`) / `src/utils/hooks.ts` ->
`processHookJSONOutput`.

Upstream runs these hooks only when the gate's answer is still 'ask' and no one can be
asked. The first hook that states a decision wins:

* `allow` — the call runs, on the hook's `updatedInput` if it gave one, and the hook's
  `updatedPermissions` are persisted (`persistPermissionUpdates`) and applied to the run's
  context (`applyPermissionUpdates`);
* `deny` — refused with the hook's message, and `interrupt: true` aborts the turn;
* no decision, an invalid one, or a hook that fails — fall through to the headless deny.

A hook allow approves a call no rule covers, and overrides an ask rule or a safety check:
that is upstream's v2.1.88 behaviour, kept. Two TDD guards are added, each recorded as a
divergence:

* **A rewritten input is re-checked.** The phase tier ran on the input the gate saw; a hook
  `updatedInput` could swap `tests/test_x.py` for `calc.py` after that. The TDD tier and the
  deny rules (`check_rule_based_permissions`, deny results only — the re-check v2.1.110
  later added upstream) run on it, and a deny there refuses the call without applying the
  hook's updates.
* **Applied updates are re-stripped.** `strip_dangerous_permissions` runs on the context the
  updates produce, so a hook cannot re-open the interpreter loophole with
  `addRules: Bash(python:*)`. `setMode` is applied unguarded, as v2.1.88 does; the phase
  tier sits above `bypassPermissions` regardless.

The permission layer's user-facing wording, in one place.

Ported from claude-code v2.1.88:

* `src/utils/permissions/permissions.ts` -> `createPermissionRequestMessage`,
  `permissionRuleSourceDisplayString`
* `src/utils/permissions/permissionRuleParser.ts` -> `permissionRuleValueToString`
* `src/utils/settings/constants.ts` -> `getSettingSourceDisplayNameLowercase`
* `src/utils/permissions/PermissionMode.ts` -> `permissionModeTitle`
* `src/utils/messages.ts` -> `DENIAL_WORKAROUND_GUIDANCE`, `AUTO_REJECT_MESSAGE`,
  `DONT_ASK_REJECT_MESSAGE`

These strings are the model's only feedback about why a call did not run, so they are
ported verbatim, except that the agent is named where upstream names Claude / Claude Code.
The classifier, permission-prompt-tool and sandbox-override reasons are not ported: none of
those mechanisms exists in this loop.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
    permission_mode_title,
)
from app.utils.messages import (
    auto_reject_message,
    dont_ask_reject_message,
)
from app.utils.permissions.classifier_decision import is_auto_mode_allowlisted_tool
from app.utils.permissions.dangerous_patterns import strip_dangerous_permissions
from app.utils.permissions.denial_tracking import (
    MAX_TOTAL_DENIALS,
    DenialTrackingState,
    create_denial_tracking_state,
    record_denial,
    record_success,
    should_fallback_to_prompting,
)
from app.utils.permissions.permission_rule_parser import permission_rule_value_to_string
from app.utils.permissions.permission_update import (
    apply_permission_updates,
    parse_permission_updates,
    persist_permission_updates,
    settings_locations_for,
)
from app.utils.settings.constants import SOURCE_DISPLAY_NAMES

if TYPE_CHECKING:
    from app.tool import Tool, ToolContext


async def has_permissions_to_use_tool(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> PermissionResult:
    """
    `hasPermissionsToUseTool`, the outer gate: the inner decision; in auto mode an allow
    resets the denial streak and an 'ask' (not denied by `dontAsk`) goes to the classifier
    (`utils/permissions/permissions.py`) before the headless deny; any other 'ask' is resolved by
    `resolve_ask_without_prompt`.
    """

    result = await _has_permissions_to_use_tool_inner(tool, input_args, context)
    perm_ctx = extract_permission_context(context)
    if result.behavior == PermissionBehavior.ALLOW:
        reset_denials_on_allow(context, perm_ctx)
        return result
    if result.behavior == PermissionBehavior.ASK and perm_ctx.mode == PermissionMode.AUTO:
        return await auto_mode_decision(result, tool, input_args, context, perm_ctx)
    return await resolve_ask_without_prompt(result, tool, input_args, context, tool_use_id)


async def resolve_ask_without_prompt(
    result: PermissionResult,
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
    tool_use_id: str = "",
) -> PermissionResult:
    """
    The outer gate's handling of an 'ask', split out so a PreToolUse hook's own 'ask'
    (upstream's `forceDecision`) goes through it too. Only an 'ask' is transformed, in this
    order: `dontAsk` denies; a context that cannot prompt (`should_avoid_permission_prompts`)
    gives PermissionRequest hooks the first word and otherwise denies. Anything else is
    returned unchanged — an interactive caller shows its prompt.
    """
    if result.behavior != PermissionBehavior.ASK:
        return result
    perm_ctx = extract_permission_context(context)
    if perm_ctx.mode == PermissionMode.DONT_ASK:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=dont_ask_reject_message(tool.name),
            decision_reason={"type": "mode", "mode": PermissionMode.DONT_ASK},
        )
    if perm_ctx.should_avoid_permission_prompts:
        hook_decision = await run_permission_request_hooks_for_headless_agent(
            tool, input_args, tool_use_id, context, perm_ctx.mode
        )
        if hook_decision is not None:
            return hook_decision
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=auto_reject_message(tool.name),
            decision_reason={"type": "asyncAgent", "reason": "Permission prompts are not available in this context"},
        )
    return result


def _updated_input_or_fallback(result: PermissionResult, input_args: dict[str, Any]) -> dict[str, Any]:
    """`getUpdatedInputOrFallback`."""
    return result.updated_input if result.updated_input is not None else input_args


async def _has_permissions_to_use_tool_inner(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    """
    `hasPermissionsToUseToolInner`, after the TDD tier:

    1a. a tool-wide deny rule denies; 1b. a tool-wide ask rule asks;
    1c. the tool's own `check_permissions` (an exception leaves it at passthrough);
    1d. its deny stands; 1e. an ask from a tool that requires interaction stands;
    1f. an ask from a content ask rule stands; 1g. an ask from a safety check stands —
        all four before any mode, so bypass cannot skip them;
    2a. `bypassPermissions` (or `plan` when bypass is available) allows;
    2b. a tool-wide allow rule allows;
    3.  passthrough becomes ask.
    """
    if context.cancel.cancelled:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message="Operation cancelled.",
            decision_reason={"type": "other", "reason": "cancelled"},
        )

    phase_denial = tdd_phase_denial(tool, input_args, context)
    if phase_denial is not None:
        return phase_denial

    perm_ctx = extract_permission_context(context)

    deny_rule = get_deny_rule_for_tool(perm_ctx, tool.name)
    if deny_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason={"type": "rule", "rule": deny_rule},
        )

    ask_rule = get_ask_rule_for_tool(perm_ctx, tool.name)
    if ask_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=create_permission_request_message(tool.name),
            decision_reason={"type": "rule", "rule": ask_rule},
        )

    tool_perm_res = await tool_permission_result(tool, input_args, context)

    if tool_perm_res.behavior == PermissionBehavior.DENY:
        return tool_perm_res

    if tool_perm_res.behavior == PermissionBehavior.ASK and requires_user_interaction(tool, input_args):
        return tool_perm_res
    if is_ask_rule_reason(tool_perm_res) or is_safety_check_ask(tool_perm_res):
        return tool_perm_res

    mode = perm_ctx.mode
    should_bypass = mode == PermissionMode.BYPASS_PERMISSIONS or (
        mode == PermissionMode.PLAN and perm_ctx.is_bypass_permissions_mode_available
    )
    if should_bypass:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=_updated_input_or_fallback(tool_perm_res, input_args),
            decision_reason={"type": "mode", "mode": mode},
        )

    allow_rule = get_allow_rule_for_tool(perm_ctx, tool.name)
    if allow_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=_updated_input_or_fallback(tool_perm_res, input_args),
            decision_reason={"type": "rule", "rule": allow_rule},
        )

    if tool_perm_res.behavior == PermissionBehavior.PASSTHROUGH:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=create_permission_request_message(tool.name, tool_perm_res.decision_reason),
            updated_input=tool_perm_res.updated_input,
            decision_reason=tool_perm_res.decision_reason,
        )
    return tool_perm_res


logger = logging.getLogger("TDDOrchestrator.Permissions")


def mcp_info_from_string(tool_string: str) -> tuple[str, str | None] | None:
    """
    `mcpStringUtils.ts` -> `mcpInfoFromString`: `mcp__server__tool` -> ("server", "tool").

    Everything after the server name is the tool name, double underscores preserved.
    """
    parts = tool_string.split("__")
    if len(parts) < 2 or parts[0] != "mcp" or not parts[1]:
        return None
    tool = "__".join(parts[2:]) if len(parts) > 2 else None
    return parts[1], tool


def tool_name_matches_rule(rule_tool_name: str, tool_name: str) -> bool:
    """
    The name half of upstream `toolMatchesRule`: an exact name, or an MCP server rule
    (`mcp__server` or `mcp__server__*`) matching every tool on that server. A plain prefix
    is not a match: a `Bash` rule must not cover `BashOutput`.
    """
    if rule_tool_name == tool_name:
        return True
    rule_info = mcp_info_from_string(rule_tool_name)
    tool_info = mcp_info_from_string(tool_name)
    return (
        rule_info is not None
        and tool_info is not None
        and rule_info[1] in (None, "*")
        and rule_info[0] == tool_info[0]
    )


def rule_matches(rule: PermissionRule, tool_name: str, input_content: str | None = None) -> bool:
    """
    Check if a permission rule matches a tool call.

    The tool name must match as `tool_name_matches_rule` defines it. If rule.rule_content
    is defined, input_content must match (exact match or glob-style prefix with '*').
    """
    if not tool_name_matches_rule(rule.tool_name, tool_name):
        return False

    if rule.rule_content is None:
        # Blanket rule for the entire tool
        return True

    if input_content is None:
        return False

    if rule.rule_content.endswith("*"):
        prefix = rule.rule_content[:-1]
        return input_content.startswith(prefix)

    return rule.rule_content == input_content


def get_rule_for_tool(
    rules: Sequence[PermissionRule],
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """
    Find the first rule in the sequence that matches the tool and optional content.
    """
    for rule in rules:
        if rule_matches(rule, tool_name, input_content):
            return rule
    return None


def get_deny_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-deny rule."""
    return get_rule_for_tool(context.always_deny_rules, tool_name, input_content)


def get_ask_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-ask rule."""
    return get_rule_for_tool(context.always_ask_rules, tool_name, input_content)


def get_allow_rule_for_tool(
    context: ToolPermissionContext,
    tool_name: str,
    input_content: str | None = None,
) -> PermissionRule | None:
    """Check if the tool or content is matched by an always-allow rule."""
    return get_rule_for_tool(context.always_allow_rules, tool_name, input_content)


def extract_permission_context(context: ToolContext) -> ToolPermissionContext:
    """
    Extract ToolPermissionContext from ToolContext or AppState, falling back to default.
    """
    if hasattr(context, "permission_context") and isinstance(context.permission_context, ToolPermissionContext):
        return context.permission_context

    if hasattr(context, "get_app_state"):
        try:
            app_state = context.get_app_state()
            if hasattr(app_state, "tool_permission_context") and isinstance(
                app_state.tool_permission_context, ToolPermissionContext
            ):
                return app_state.tool_permission_context
        except Exception:
            pass

    return ToolPermissionContext()


def tdd_phase_denial(tool: Tool, input_args: dict[str, Any], context: ToolContext) -> PermissionResult | None:
    """
    The TDD managed tier: a deny when the ledger's phase forbids this call, else `None`.

    It runs before every rule, mode and hook — in the gate and in the rule-only re-check of
    a hook verdict alike — so that no allow path can reach past it. It fails closed: a check
    that raises is a refusal, because a phase rule that silently stops applying is barrier 2
    switched off.
    """
    try:
        ledger = getattr(context.get_app_state(), "phase_ledger", None)
        if ledger is None:
            return None
        from app.tdd.phase_permissions import check_tdd_phase_permission

        permitted, reason = check_tdd_phase_permission(tool, input_args, ledger)
    except Exception as exc:
        logger.exception("TDD phase check failed for %s", tool.name)
        reason = f"TDD phase check failed for {tool.name}: {exc}"
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=reason,
            decision_reason={"type": "phase_rule", "phase": None, "reason": reason},
        )
    if permitted:
        return None
    return PermissionResult(
        behavior=PermissionBehavior.DENY,
        message=reason,
        decision_reason={"type": "phase_rule", "phase": ledger.phase, "reason": reason},
    )


def requires_user_interaction(tool: Tool, input_args: dict[str, Any]) -> bool:
    """`tool.requiresUserInteraction?.()`, for predicates with or without the input."""
    fn = getattr(tool, "requires_user_interaction", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(input_args))
    except TypeError:
        return bool(fn())


def is_ask_rule_reason(result: PermissionResult) -> bool:
    """Step 1f's test: an 'ask' whose reason is a rule whose own behavior is 'ask'."""
    reason = result.decision_reason or {}
    rule = reason.get("rule")
    return (
        result.behavior == PermissionBehavior.ASK
        and reason.get("type") == "rule"
        and isinstance(rule, PermissionRule)
        and rule.rule_behavior == PermissionBehavior.ASK
    )


def is_safety_check_ask(result: PermissionResult) -> bool:
    """Step 1g's test: an 'ask' raised by a bypass-immune safety check."""
    return result.behavior == PermissionBehavior.ASK and (result.decision_reason or {}).get("type") == "safetyCheck"


async def tool_permission_result(tool: Tool, input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
    """
    Step 1c: the tool's own `check_permissions`. Upstream seeds a passthrough and keeps it
    when the check throws (logged, not a deny); cancellation propagates.
    """
    result = PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)
    try:
        res = tool.check_permissions(input_args, context)
        if asyncio.iscoroutine(res):
            res = await res
        if isinstance(res, PermissionResult):
            result = res
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Permission check of %s raised; treating it as passthrough", tool.name)
    return result


async def check_rule_based_permissions(
    tool: Tool,
    input_args: dict[str, Any],
    context: ToolContext,
) -> PermissionResult | None:
    """
    `checkRuleBasedPermissions`: only the rule-based steps of the pipeline — the subset
    `bypassPermissions` respects — preceded by the TDD managed tier.

    Returns a deny or an ask, or `None` when no rule objects. It deliberately does **not**
    run step 1e (the caller checks `requires_user_interaction`), any mode step, always-allow
    rules, passthrough-to-ask, dontAsk, the headless deny or PermissionRequest hooks. It is
    what a PreToolUse hook's verdict is re-checked against.

    0.  TDD phase tier -> deny
    1a. tool-wide deny rule -> deny
    1b. tool-wide ask rule -> ask
    1c. the tool's own check; 1d. its deny -> deny
    1f. an ask from a content ask rule -> ask
    1g. an ask from a safety check -> ask
    """
    phase_denial = tdd_phase_denial(tool, input_args, context)
    if phase_denial is not None:
        return phase_denial

    perm_ctx = extract_permission_context(context)

    deny_rule = get_deny_rule_for_tool(perm_ctx, tool.name)
    if deny_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {tool.name} has been denied.",
            decision_reason={"type": "rule", "rule": deny_rule},
        )

    ask_rule = get_ask_rule_for_tool(perm_ctx, tool.name)
    if ask_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=create_permission_request_message(tool.name),
            decision_reason={"type": "rule", "rule": ask_rule},
        )

    result = await tool_permission_result(tool, input_args, context)
    if result.behavior == PermissionBehavior.DENY:
        return result
    if is_ask_rule_reason(result) or is_safety_check_ask(result):
        return result
    return None


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
    from app.utils.permissions.yolo_classifier import (
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


PERMISSION_REQUEST_EVENT = "PermissionRequest"


def permission_request_decision(outcome: Any) -> Mapping[str, Any] | None:
    """
    The hook decision carried by a dispatcher outcome, if it is a valid one. An allow whose
    `updatedPermissions` fail the schema is no decision at all, as upstream's Zod parse
    rejects the whole hook output.
    """
    decision = getattr(outcome, "permission_request_result", None)
    if not isinstance(decision, Mapping) or decision.get("behavior") not in ("allow", "deny"):
        return None
    updates = decision.get("updatedPermissions")
    if updates is not None and parse_permission_updates(updates) is None:
        logger.warning("Ignoring a PermissionRequest decision with malformed updatedPermissions.")
        return None
    return decision


def apply_hook_permission_updates(updates: Any, context: ToolContext) -> None:
    """Persist, then apply and re-strip, a hook's `updatedPermissions`."""
    parsed = parse_permission_updates(updates) or ()
    if not parsed:
        return
    current = extract_permission_context(context)
    locations = settings_locations_for(current)
    if locations is not None:
        persist_permission_updates(parsed, locations)

    def apply(state: Any) -> Any:
        updated = strip_dangerous_permissions(apply_permission_updates(state.tool_permission_context, parsed))
        return replace(state, tool_permission_context=updated)

    context.set_app_state(apply)


async def run_permission_request_hooks_for_headless_agent(
    tool: Tool,
    input_args: dict[str, Any],
    tool_use_id: str,
    context: ToolContext,
    permission_mode: PermissionMode | str,
) -> PermissionResult | None:
    """`runPermissionRequestHooksForHeadlessAgent`: the first hook decision, or `None`."""
    from app.utils.hooks.hooks import execute_permission_request_hooks

    try:
        async for result in execute_permission_request_hooks(
            context, tool.name, tool_use_id, input_args, str(permission_mode), None,
            getattr(context, "cancel", None),
        ):
            decision = permission_request_decision(result)
            if decision is None:
                continue
            return await _apply_decision(decision, tool, input_args, context)
    except Exception:
        # If hooks fail, fall through to the auto-deny rather than crashing.
        logger.exception("PermissionRequest hook failed for headless agent")
    return None


async def _apply_decision(
    decision: Mapping[str, Any], tool: Tool, input_args: dict[str, Any], context: ToolContext
) -> PermissionResult:
    if decision["behavior"] == "allow":
        updated = decision.get("updatedInput")
        final_input = dict(updated) if isinstance(updated, Mapping) else input_args
        if isinstance(updated, Mapping):
            recheck = await check_rule_based_permissions(tool, final_input, context)
            if recheck is not None and recheck.behavior == PermissionBehavior.DENY:
                return recheck
        apply_hook_permission_updates(decision.get("updatedPermissions"), context)
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=final_input,
            decision_reason={"type": "hook", "hookName": PERMISSION_REQUEST_EVENT},
        )
    message = decision.get("message")
    if decision.get("interrupt") is True:
        logger.info("Hook interrupt: tool=%s hookMessage=%s", tool.name, message)
        context.cancel.cancel()
    return PermissionResult(
        behavior=PermissionBehavior.DENY,
        message=str(message) if message else "Permission denied by hook",
        decision_reason={"type": "hook", "hookName": PERMISSION_REQUEST_EVENT, "reason": message},
    )


def permission_rule_source_display_string(source: PermissionRuleSource | str) -> str:
    """`permissionRuleSourceDisplayString`."""
    try:
        return SOURCE_DISPLAY_NAMES[PermissionRuleSource(source)]
    except ValueError:
        return str(source)


def _subcommand_message(tool_name: str, reasons: Any) -> str:
    from app.utils.bash.commands import extract_output_redirections

    needs: list[str] = []
    if isinstance(reasons, Mapping):
        for cmd, result in reasons.items():
            if getattr(result, "behavior", None) in ("ask", "passthrough"):
                if tool_name == "Bash":
                    # Strip output redirections for display, so filenames do not read as commands.
                    extraction = extract_output_redirections(cmd)
                    needs.append(extraction.command_without_redirections if extraction.redirections else cmd)
                else:
                    needs.append(cmd)
    if needs:
        part = "part" if len(needs) == 1 else "parts"
        verb = "requires" if len(needs) == 1 else "require"
        return (
            f"This {tool_name} command contains multiple operations. "
            f"The following {part} {verb} approval: {', '.join(needs)}"
        )
    return f"This {tool_name} command contains multiple operations that require approval"


def create_permission_request_message(
    tool_name: str,
    decision_reason: Mapping[str, Any] | None = None,
) -> str:
    """`createPermissionRequestMessage`: why a call needs approval, per reason type."""
    if decision_reason:
        reason_type = decision_reason.get("type")
        if reason_type == "hook":
            hook_name = decision_reason.get("hookName")
            reason = decision_reason.get("reason")
            if reason:
                return f"Hook '{hook_name}' blocked this action: {reason}"
            return f"Hook '{hook_name}' requires approval for this {tool_name} command"
        if reason_type == "rule":
            rule = decision_reason.get("rule")
            if isinstance(rule, PermissionRule):
                rule_string = permission_rule_value_to_string(rule.tool_name, rule.rule_content)
                source_string = permission_rule_source_display_string(rule.source)
                return (
                    f"Permission rule '{rule_string}' from {source_string} requires approval "
                    f"for this {tool_name} command"
                )
        if reason_type == "subcommandResults":
            return _subcommand_message(tool_name, decision_reason.get("reasons"))
        if reason_type in ("workingDir", "safetyCheck", "other", "asyncAgent"):
            return str(decision_reason.get("reason"))
        if reason_type == "mode":
            title = permission_mode_title(decision_reason.get("mode", PermissionMode.DEFAULT))
            return f"Current permission mode ({title}) requires approval for this {tool_name} command"
    return f"The agent requested permissions to use {tool_name}, but you haven't granted it yet."


__all__ = [
    "AutoModeAbortError",
    "DENIAL_LIMIT_ABORT",
    "PERMISSION_REQUEST_EVENT",
    "SAFETY_CHECK_HEADLESS_REASON",
    "TRANSCRIPT_TOO_LONG_ABORT",
    "TRANSCRIPT_TOO_LONG_REASON",
    "apply_hook_permission_updates",
    "auto_mode_decision",
    "create_permission_request_message",
    "current_denial_state",
    "handle_denial_limit_exceeded",
    "has_permissions_to_use_tool",
    "permission_request_decision",
    "permission_rule_source_display_string",
    "persist_denial_state",
    "reset_denials_on_allow",
    "resolve_ask_without_prompt",
    "run_permission_request_hooks_for_headless_agent",
]
