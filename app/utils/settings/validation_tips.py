"""
A fixing tip for a settings validation error.

Ported from `reference/claude-code/src/utils/settings/validationTips.ts` -> `ValidationTip`,
`TipContext`, `TIP_MATCHERS`, `getValidationTip`. The suggestions are upstream's, in its order, for
the keys this build reads (`app/utils/settings/types.py` -> `SUPPORTED_SETTINGS_KEYS`); the hook
example names this build's `WriteFile` tool.

*Divergences:* the documentation links (`docLink`, `PATH_DOC_LINKS`, Claude Code's pages) are left
out, as TDDAgents has no such pages; the tips for keys this build does not read (`apiKeyHelper`,
`cleanupPeriodDays`, `env.*`) are left out; the `additionalDirectories` tip drops "You can also use
--add-dir flag or /add-dir command" (neither exists here).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ValidationTip:
    """`ValidationTip`."""

    suggestion: str | None = None
    doc_link: str | None = None


@dataclass(frozen=True, slots=True)
class TipContext:
    """`TipContext`."""

    path: str
    code: str
    expected: str | None = None
    received: Any = None
    enum_values: Sequence[str] | None = None
    message: str | None = None
    value: Any = None


_TipMatcher = tuple[Callable[[TipContext], bool], ValidationTip]

TIP_MATCHERS: tuple[_TipMatcher, ...] = (
    (lambda c: c.path == "permissions.defaultMode" and c.code == "invalid_value",
     ValidationTip('Valid modes: "acceptEdits" (ask before file changes), "plan" (analysis only), "bypassPermissions" '
                   '(auto-accept all), or "default" (standard behavior)')),
    (lambda c: c.path in ("permissions.allow", "permissions.deny") and c.code == "invalid_type"
     and c.expected == "array",
     ValidationTip('Permission rules must be in an array. Format: ["Tool(specifier)"]. Examples: '
                   '["Bash(npm run build)", "Edit(docs/**)", "ReadFile(~/.zshrc)"]. Use * for wildcards.')),
    (lambda c: "hooks" in c.path and c.code == "invalid_type",
     ValidationTip('Hooks use a matcher + hooks array. The matcher is a string: a tool name ("Bash"), pipe-separated '
                   'list ("Edit|WriteFile"), or empty to match all. Example: {"PostToolUse": [{"matcher": '
                   '"Edit|WriteFile", "hooks": [{"type": "command", "command": "echo Done"}]}]}')),
    (lambda c: c.code == "invalid_type" and c.expected == "boolean",
     ValidationTip('Use true or false without quotes. Example: "includeCoAuthoredBy": true')),
    (lambda c: c.code == "unrecognized_keys",
     ValidationTip("Check for typos or refer to the documentation for valid fields")),
    (lambda c: c.code == "invalid_value" and c.enum_values is not None, ValidationTip()),
    (lambda c: c.code == "invalid_type" and c.expected == "object" and c.received is None and c.path == "",
     ValidationTip("Check for missing commas, unmatched brackets, or trailing commas. Use a JSON validator to "
                   "identify the exact syntax error.")),
    (lambda c: c.path == "permissions.additionalDirectories" and c.code == "invalid_type",
     ValidationTip('Must be an array of directory paths. Example: ["~/projects", "/tmp/workspace"].')),
)


def get_validation_tip(context: TipContext) -> ValidationTip | None:
    """`getValidationTip`: the first matching tip; an enum's values when it has no suggestion of its own."""
    tip = next((tip for matches, tip in TIP_MATCHERS if matches(context)), None)
    if tip is None:
        return None
    if context.code == "invalid_value" and context.enum_values and not tip.suggestion:
        values = ", ".join(f'"{v}"' for v in context.enum_values)
        return ValidationTip(suggestion=f"Valid values: {values}", doc_link=tip.doc_link)
    return tip


__all__ = ["TIP_MATCHERS", "TipContext", "ValidationTip", "get_validation_tip"]
