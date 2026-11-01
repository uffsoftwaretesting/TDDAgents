"""
The EnterPlanMode dialog.

Ported from `reference/claude-code/src/components/permissions/EnterPlanModePermissionRequest/
EnterPlanModePermissionRequest.tsx`, its text verbatim with "Claude" -> "TDDAgents".
"""

from __future__ import annotations

from app.components.permissions.permission_dialog import PermissionDialog

ENTER_PLAN_MODE_BODY = (
    "TDDAgents wants to enter plan mode to explore and design an implementation approach.\n"
    "\n"
    "In plan mode, TDDAgents will:\n"
    " · Explore the codebase thoroughly\n"
    " · Identify existing patterns\n"
    " · Design an implementation strategy\n"
    " · Present a plan for your approval\n"
    "\n"
    "No code changes will be made until you approve the plan."
)


def enter_plan_mode_permission_request() -> PermissionDialog:
    return PermissionDialog(title="Enter plan mode?", body=ENTER_PLAN_MODE_BODY, question="")
