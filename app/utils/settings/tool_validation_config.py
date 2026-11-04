"""
Which tools' permission rules get a pattern check, and the tools with a check of their own.

Ported from `reference/claude-code/src/utils/settings/toolValidationConfig.ts` ->
`ToolValidationConfig`, `TOOL_VALIDATION_CONFIG`, `isFilePatternTool`, `isBashPrefixTool`,
`getCustomValidation`. The file-pattern tools carry this build's names (`ReadFile`, `WriteFile` for
upstream's `Read`, `Write`); the custom checks' texts are upstream's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RuleValidation:
    """`validatePermissionRule`'s result: valid, or the error with a suggestion and examples."""

    valid: bool
    error: str | None = None
    suggestion: str | None = None
    examples: tuple[str, ...] = field(default_factory=tuple)


VALID = RuleValidation(valid=True)


def _web_search(content: str) -> RuleValidation:
    """WebSearch doesn't support wildcards or complex patterns."""
    if "*" in content or "?" in content:
        return RuleValidation(False, "WebSearch does not support wildcards", "Use exact search terms without * or ?",
                              ("WebSearch(claude ai)", "WebSearch(typescript tutorial)"))
    return VALID


def _web_fetch(content: str) -> RuleValidation:
    """WebFetch uses the `domain:` prefix for hostname-based permissions."""
    if "://" in content or content.startswith("http"):
        return RuleValidation(False, "WebFetch permissions use domain format, not URLs", 'Use "domain:hostname" format',
                              ("WebFetch(domain:example.com)", "WebFetch(domain:github.com)"))
    if not content.startswith("domain:"):
        return RuleValidation(False, 'WebFetch permissions must use "domain:" prefix', 'Use "domain:hostname" format',
                              ("WebFetch(domain:example.com)", "WebFetch(domain:*.google.com)"))
    return VALID


#: `TOOL_VALIDATION_CONFIG.filePatternTools`.
FILE_PATTERN_TOOLS: tuple[str, ...] = ("ReadFile", "WriteFile", "Edit", "Glob", "NotebookRead", "NotebookEdit")
#: `TOOL_VALIDATION_CONFIG.bashPrefixTools`.
BASH_PREFIX_TOOLS: tuple[str, ...] = ("Bash",)
#: `TOOL_VALIDATION_CONFIG.customValidation`.
CUSTOM_VALIDATION: dict[str, Callable[[str], RuleValidation]] = {"WebSearch": _web_search, "WebFetch": _web_fetch}


def is_file_pattern_tool(tool_name: str) -> bool:
    """`isFilePatternTool`."""
    return tool_name in FILE_PATTERN_TOOLS


def is_bash_prefix_tool(tool_name: str) -> bool:
    """`isBashPrefixTool`."""
    return tool_name in BASH_PREFIX_TOOLS


def get_custom_validation(tool_name: str) -> Callable[[str], RuleValidation] | None:
    """`getCustomValidation`."""
    return CUSTOM_VALIDATION.get(tool_name)


__all__ = [
    "BASH_PREFIX_TOOLS",
    "CUSTOM_VALIDATION",
    "FILE_PATTERN_TOOLS",
    "RuleValidation",
    "VALID",
    "get_custom_validation",
    "is_bash_prefix_tool",
    "is_file_pattern_tool",
]
