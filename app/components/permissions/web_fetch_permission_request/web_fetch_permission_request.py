"""
The WebFetch dialog.

Ported from `reference/claude-code/src/components/permissions/WebFetchPermissionRequest/
WebFetchPermissionRequest.tsx`: title `Fetch`, `renderToolUseMessage(input, verbose)` (the REPL is
not verbose, so the URL alone), the description, `PermissionRuleExplanation` for a 'tool', and
"Do you want to allow TDDAgents to fetch this content?" (product substitution).
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.permission_rule_explanation import (
    permission_rule_explanation,
)
from app.types.permissions import PermissionResult


def web_fetch_permission_request(tool: Any, input_args: dict[str, Any], result: PermissionResult) -> PermissionDialog:
    return PermissionDialog(
        title="Fetch",
        body=tool.render_tool_use_message(input_args, False) or "",
        description=tool.description(input_args),
        explanation=permission_rule_explanation(result.decision_reason, "tool") or "",
        question="Do you want to allow TDDAgents to fetch this content?",
    )
