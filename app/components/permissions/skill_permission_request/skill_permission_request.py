"""
The Skill dialog.

Ported from `reference/claude-code/src/components/permissions/SkillPermissionRequest/
SkillPermissionRequest.tsx`: title `Use skill "<name>"?`, "TDDAgents may use instructions,
code, or files from this Skill." (product substitution), the skill's description, and
`PermissionRuleExplanation` for a 'tool', then `PermissionPrompt`'s default question.
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.permission_rule_explanation import (
    permission_rule_explanation,
)
from app.types.permissions import PermissionResult


def skill_permission_request(input_args: dict[str, Any], result: PermissionResult, context: Any) -> PermissionDialog:
    skill = str(input_args.get("skill") or "").lstrip("/")
    registry = getattr(context, "skill_registry", None)
    command = registry.find_command(skill) if registry is not None else None
    return PermissionDialog(
        title=f'Use skill "{skill}"?',
        body="TDDAgents may use instructions, code, or files from this Skill.",
        description=str(getattr(command, "description", "") or ""),
        explanation=permission_rule_explanation(result.decision_reason, "tool") or "",
    )
