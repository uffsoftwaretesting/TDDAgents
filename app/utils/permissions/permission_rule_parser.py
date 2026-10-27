"""
Permission rule strings.

Ported from `reference/claude-code/src/utils/permissions/permissionRuleParser.ts` ->
`escapeRuleContent`, `permissionRuleValueToString`.
"""

from __future__ import annotations


def escape_rule_content(content: str) -> str:
    """`escapeRuleContent`: backslashes first, then parentheses."""
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def permission_rule_value_to_string(tool_name: str, rule_content: str | None) -> str:
    """`permissionRuleValueToString`: `Bash` or `Bash(npm install)`, content re-escaped."""
    if not rule_content:
        return tool_name
    return f"{tool_name}({escape_rule_content(rule_content)})"
