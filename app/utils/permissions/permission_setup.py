"""
Permission-mode transitions for plan mode.

Ported from `reference/claude-code/src/utils/permissions/permissionSetup.ts` ->
`prepareContextForPlanMode`, `transitionPermissionMode`, and `bootstrap/state.ts` ->
`handlePlanModeTransition`, `setHasExitedPlanMode`. Upstream keeps the two plan-mode flags in
process state; here they live on the run's `AppState` (`has_exited_plan_mode`,
`needs_plan_mode_exit_attachment`).

*Divergence:* the `TRANSCRIPT_CLASSIFIER` branches (plan mode with auto active, and the
auto-mode transitions inside `transitionPermissionMode`) are not ported: entering plan mode from
`auto` stores `prePlanMode = auto` and plan mode runs without the classifier.
"""

from __future__ import annotations

import dataclasses

from app.state.app_state_store import AppState
from app.types.permissions import PermissionMode, ToolPermissionContext


def prepare_context_for_plan_mode(context: ToolPermissionContext) -> ToolPermissionContext:
    """`prepareContextForPlanMode`: stash the current mode as `prePlanMode` (plain plan entry)."""
    if context.mode == PermissionMode.PLAN:
        return context
    return dataclasses.replace(context, pre_plan_mode=context.mode)


def transition_permission_mode(
    from_mode: PermissionMode, to_mode: PermissionMode, context: ToolPermissionContext
) -> ToolPermissionContext:
    """
    `transitionPermissionMode`, the context half: entering plan stashes `prePlanMode`, leaving it
    clears `prePlanMode`. The caller sets the mode on the returned context.
    """
    if from_mode == to_mode:
        return context
    if to_mode == PermissionMode.PLAN:
        return prepare_context_for_plan_mode(context)
    if from_mode == PermissionMode.PLAN and context.pre_plan_mode is not None:
        return dataclasses.replace(context, pre_plan_mode=None)
    return context


def handle_plan_mode_transition(state: AppState, from_mode: PermissionMode, to_mode: PermissionMode) -> AppState:
    """
    `handlePlanModeTransition` + `setHasExitedPlanMode`: entering plan clears a pending exit
    attachment; leaving it asks for one and records that plan mode was left.
    """
    if to_mode == PermissionMode.PLAN and from_mode != PermissionMode.PLAN:
        return dataclasses.replace(state, needs_plan_mode_exit_attachment=False)
    if from_mode == PermissionMode.PLAN and to_mode != PermissionMode.PLAN:
        return dataclasses.replace(state, needs_plan_mode_exit_attachment=True, has_exited_plan_mode=True)
    return state


def set_permission_mode(state: AppState, to_mode: PermissionMode) -> AppState:
    """
    A mode change as upstream's callers make it (`cyclePermissionMode`, `EnterPlanModeTool.call`,
    `/plan`): the plan-mode flags, the context transition, then the new mode.
    """
    context = state.tool_permission_context
    from_mode = context.mode
    if from_mode == to_mode:
        return state
    state = handle_plan_mode_transition(state, from_mode, to_mode)
    context = transition_permission_mode(from_mode, to_mode, context)
    return dataclasses.replace(state, tool_permission_context=dataclasses.replace(context, mode=to_mode))


__all__ = [
    "handle_plan_mode_transition",
    "prepare_context_for_plan_mode",
    "set_permission_mode",
    "transition_permission_mode",
]
