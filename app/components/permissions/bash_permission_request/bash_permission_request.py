"""
The Bash dialog: the whole command, then why it asks.

Ported from `reference/claude-code/src/components/permissions/BashPermissionRequest/
BashPermissionRequest.tsx`: title `Bash command`, the command from
`BashTool.renderToolUseMessage(..., verbose: true)` ("always show the full command"), the
description (`description || 'Run shell command'`) and `PermissionRuleExplanation` for a
'command'. Not ported: the sed-edit dialog, the sandbox title, and the destructive-command
warning (behind the default-off `tengu_destructive_command_warning` flag).
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.permission_rule_explanation import (
    permission_rule_explanation,
)
from app.types.permissions import PermissionResult


def bash_permission_request(tool: Any, input_args: dict[str, Any], result: PermissionResult) -> PermissionDialog:
    return PermissionDialog(
        title="Bash command",
        body=tool.render_tool_use_message(input_args, True) or "",
        description=tool.description(input_args),
        explanation=permission_rule_explanation(result.decision_reason, "command") or "",
    )
