"""
Checking a permission rule's format before it is loaded.

Ported from `reference/claude-code/src/utils/settings/permissionValidation.ts` -> `isEscaped`,
`countUnescapedChar`, `hasUnescapedEmptyParens`, `validatePermissionRule`, and the message
`PermissionRuleSchema`'s `superRefine` builds from it (`permission_rule_issue_message`). Every text is
upstream's.
"""

from __future__ import annotations

import re

from app.services.mcp.normalization import mcp_info_from_string
from app.utils.settings.settings import permission_rule_value_from_string
from app.utils.settings.tool_validation_config import (
    VALID,
    RuleValidation,
    get_custom_validation,
    is_bash_prefix_tool,
    is_file_pattern_tool,
)


def _is_escaped(text: str, index: int) -> bool:
    """`isEscaped`: preceded by an odd number of backslashes."""
    count = 0
    j = index - 1
    while j >= 0 and text[j] == "\\":
        count += 1
        j -= 1
    return count % 2 != 0


def count_unescaped_char(text: str, char: str) -> int:
    """`countUnescapedChar`."""
    return sum(1 for i, c in enumerate(text) if c == char and not _is_escaped(text, i))


def has_unescaped_empty_parens(text: str) -> bool:
    """`hasUnescapedEmptyParens`: an unescaped `(` directly followed by `)`."""
    return any(text[i] == "(" and text[i + 1] == ")" and not _is_escaped(text, i) for i in range(len(text) - 1))


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


#: A file pattern's wildcard at a path boundary.
_BOUNDARY_WILDCARD = re.compile(r"^\*|\*$|\*\*|/\*|\*\.|\*\)")


def validate_permission_rule(rule: str) -> RuleValidation:
    """`validatePermissionRule`."""
    if not rule or rule.strip() == "":
        return RuleValidation(False, "Permission rule cannot be empty")
    if count_unescaped_char(rule, "(") != count_unescaped_char(rule, ")"):
        return RuleValidation(False, "Mismatched parentheses",
                              "Ensure all opening parentheses have matching closing parentheses")
    if has_unescaped_empty_parens(rule):
        tool_name = rule[:rule.index("(")]
        if not tool_name:
            return RuleValidation(False, "Empty parentheses with no tool name",
                                  "Specify a tool name before the parentheses")
        return RuleValidation(False, "Empty parentheses",
                              f'Either specify a pattern or use just "{tool_name}" without parentheses',
                              (tool_name, f"{tool_name}(some-pattern)"))
    tool, content = permission_rule_value_from_string(rule)
    mcp_info = mcp_info_from_string(tool)
    if mcp_info is not None:
        server, mcp_tool = mcp_info
        if content is not None or count_unescaped_char(rule, "(") > 0:
            examples = [f"mcp__{server}", f"mcp__{server}__*"]
            if mcp_tool and mcp_tool != "*":
                examples.append(f"mcp__{server}__{mcp_tool}")
            return RuleValidation(
                False, "MCP rules do not support patterns in parentheses",
                f'Use "{tool}" without parentheses, or use "mcp__{server}__*" for all tools', tuple(examples))
        return VALID
    if not tool:
        return RuleValidation(False, "Tool name cannot be empty")
    if tool[0] != tool[0].upper():
        return RuleValidation(False, "Tool names must start with uppercase", f'Use "{_capitalize(tool)}"')
    custom = get_custom_validation(tool)
    if custom is not None and content is not None:
        result = custom(content)
        if not result.valid:
            return result
    if is_bash_prefix_tool(tool) and content is not None:
        if ":*" in content and not content.endswith(":*"):
            return RuleValidation(False, "The :* pattern must be at the end",
                                  "Move :* to the end for prefix matching, or use * for wildcard matching",
                                  ("Bash(npm run:*) - prefix matching (legacy)", "Bash(npm run *) - wildcard matching"))
        if content == ":*":
            return RuleValidation(False, "Prefix cannot be empty before :*", "Specify a command prefix before :*",
                                  ("Bash(npm:*)", "Bash(git:*)"))
    if is_file_pattern_tool(tool) and content is not None:
        if ":*" in content:
            return RuleValidation(False, 'The ":*" syntax is only for Bash prefix rules',
                                  'Use glob patterns like "*" or "**" for file matching',
                                  (f"{tool}(*.ts) - matches .ts files", f"{tool}(src/**) - matches all files in src",
                                   f"{tool}(**/*.test.ts) - matches test files"))
        if "*" in content and not _BOUNDARY_WILDCARD.search(content) and "**" not in content:
            return RuleValidation(False, "Wildcard placement might be incorrect",
                                  "Wildcards are typically used at path boundaries",
                                  (f"{tool}(*.js) - all .js files", f"{tool}(src/*) - all files directly in src",
                                   f"{tool}(src/**) - all files recursively in src"))
    return VALID


def permission_rule_issue_message(result: RuleValidation) -> str:
    """`PermissionRuleSchema`'s issue message: the error, its suggestion, then its examples."""
    message = result.error or ""
    if result.suggestion:
        message += f". {result.suggestion}"
    if result.examples:
        message += f". Examples: {', '.join(result.examples)}"
    return message


__all__ = [
    "count_unescaped_char",
    "has_unescaped_empty_parens",
    "permission_rule_issue_message",
    "validate_permission_rule",
]
