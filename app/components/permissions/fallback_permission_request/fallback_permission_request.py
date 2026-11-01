"""
The dialog for a tool without its own: `Name(input)`, the description, why it asks.

Ported from `reference/claude-code/src/components/permissions/FallbackPermissionRequest.tsx`:
title `Tool use`, `{userFacingName}({renderToolUseMessage(input, verbose: true)})` (an MCP name
already ends in ` (MCP)`), the description truncated to `DESCRIPTION_MAX_LINES` lines, and
`PermissionRuleExplanation` for a 'tool'.
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.permission_rule_explanation import (
    permission_rule_explanation,
)
from app.types.permissions import PermissionResult

#: `FallbackPermissionRequest.tsx`: the description is cut to three lines.
DESCRIPTION_MAX_LINES = 3


def fallback_permission_request(tool: Any, input_args: dict[str, Any], result: PermissionResult) -> PermissionDialog:
    rendered = tool.render_tool_use_message(input_args, True) or ""
    description_lines = tool.description(input_args).split("\n")
    description = "\n".join(description_lines[:DESCRIPTION_MAX_LINES])
    if len(description_lines) > DESCRIPTION_MAX_LINES:
        description += "…"
    return PermissionDialog(
        title="Tool use",
        body=f"{tool.user_facing_name(input_args)}({rendered})",
        description=description,
        explanation=permission_rule_explanation(result.decision_reason, "tool") or "",
    )
