"""
Why a dialog asks: the reason line under the tool call.

Ported from `reference/claude-code/src/components/permissions/PermissionRuleExplanation.tsx` ->
`stringsForDecisionReason`. Only these reason types show a line: `classifier`, `rule`, `hook`,
`safetyCheck`, `other` and `workingDir`; every other type (`subcommandResults`, `mode`,
`asyncAgent`, …) shows nothing, so the rebuilt subcommand text of a compound Bash command never
reaches the screen (upstream's external build builds it with the same regex splitter).

Divergence: upstream's dim config hints (`/permissions to update rules`, `/hooks to update`) are
dropped, because TDDAgents' REPL has neither command. Bold names are plain text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from app.types.permissions import PermissionRule
from app.utils.permissions.permission_rule_parser import permission_rule_value_to_string

ToolType = Literal["tool", "command"]


def permission_rule_explanation(decision_reason: Mapping[str, Any] | None, tool_type: ToolType) -> str | None:
    """`stringsForDecisionReason(reason, toolType).reasonString`, or `None` when it shows nothing."""
    if not decision_reason:
        return None
    kind = decision_reason.get("type")
    if kind == "classifier":
        reason = decision_reason.get("reason")
        if decision_reason.get("classifier") == "auto-mode":
            return f"Auto mode classifier requires confirmation for this {tool_type}.\n{reason}"
        return f"Classifier {decision_reason.get('classifier')} requires confirmation for this {tool_type}.\n{reason}"
    if kind == "rule":
        rule = decision_reason.get("rule")
        if not isinstance(rule, PermissionRule):
            return None
        rule_text = permission_rule_value_to_string(rule.tool_name, rule.rule_content)
        return f"Permission rule {rule_text} requires confirmation for this {tool_type}."
    if kind == "hook":
        reason = decision_reason.get("reason")
        suffix = f":\n{reason}" if reason else "."
        source = decision_reason.get("hookSource")
        label = f" [{source}]" if source else ""
        return f"Hook {decision_reason.get('hookName')} requires confirmation for this {tool_type}{suffix}{label}"
    if kind in ("safetyCheck", "other", "workingDir"):
        reason = decision_reason.get("reason")
        return str(reason) if reason else None
    return None
