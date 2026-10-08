"""
Shell permission rule parsing and matching.

Ported from `reference/claude-code/src/utils/permissions/shellRuleMatching.ts`:
`permissionRuleExtractPrefix`, `hasWildcards`, `matchWildcardPattern`,
`parsePermissionRule`. A Bash rule's content is one of three shapes:

- prefix  — legacy `npm:*`: the command is `npm` or starts with `npm `
- wildcard — `git * --dry-run`: `*` matches any run of characters, `\\*` a literal star
- exact   — anything else: the command must equal the content

The suggestion builders (`suggestionForExactCommand`, `suggestionForPrefix`) are not
ported: they feed the interactive approval UI, which this loop does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_ESCAPED_STAR_PLACEHOLDER = "\x00ESCAPED_STAR\x00"
_ESCAPED_BACKSLASH_PLACEHOLDER = "\x00ESCAPED_BACKSLASH\x00"
_REGEX_SPECIALS = re.compile(r"""[.+?^${}()|[\]\\'"]""")
_PREFIX_RULE = re.compile(r"(.+):\*\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ShellPermissionRule:
    """`ShellPermissionRule`: `value` is the command, prefix or pattern for its `type`."""

    type: Literal["exact", "prefix", "wildcard"]
    value: str


def permission_rule_extract_prefix(permission_rule: str) -> str | None:
    """`permissionRuleExtractPrefix`: `npm:*` -> `npm`."""
    m = _PREFIX_RULE.match(permission_rule)
    return m.group(1) if m else None


def has_wildcards(pattern: str) -> bool:
    """`hasWildcards`: an unescaped `*` that is not the legacy trailing `:*`."""
    if pattern.endswith(":*"):
        return False
    for i, ch in enumerate(pattern):
        if ch != "*":
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and pattern[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            return True
    return False


def match_wildcard_pattern(pattern: str, command: str, case_insensitive: bool = False) -> bool:
    """
    `matchWildcardPattern`: `*` matches anything (newlines included), `\\*` is a literal
    star and `\\\\` a literal backslash. A single trailing ` *` also matches the bare
    command, so `git *` matches `git` the way `git:*` does.
    """
    trimmed = pattern.strip()
    processed: list[str] = []
    i = 0
    while i < len(trimmed):
        ch = trimmed[i]
        if ch == "\\" and i + 1 < len(trimmed):
            nxt = trimmed[i + 1]
            if nxt == "*":
                processed.append(_ESCAPED_STAR_PLACEHOLDER)
                i += 2
                continue
            if nxt == "\\":
                processed.append(_ESCAPED_BACKSLASH_PLACEHOLDER)
                i += 2
                continue
        processed.append(ch)
        i += 1
    text = "".join(processed)

    escaped = _REGEX_SPECIALS.sub(lambda m: "\\" + m.group(0), text)
    regex = escaped.replace("*", ".*")
    regex = regex.replace(_ESCAPED_STAR_PLACEHOLDER, "\\*").replace(_ESCAPED_BACKSLASH_PLACEHOLDER, "\\\\")

    if regex.endswith(" .*") and text.count("*") == 1:
        regex = regex[:-3] + "( .*)?"

    flags = re.DOTALL | (re.IGNORECASE if case_insensitive else 0)
    return re.fullmatch(regex, command, flags) is not None


def parse_permission_rule(permission_rule: str) -> ShellPermissionRule:
    """`parsePermissionRule`: legacy prefix first, then wildcard, else exact."""
    prefix = permission_rule_extract_prefix(permission_rule)
    if prefix is not None:
        return ShellPermissionRule("prefix", prefix)
    if has_wildcards(permission_rule):
        return ShellPermissionRule("wildcard", permission_rule)
    return ShellPermissionRule("exact", permission_rule)


__all__ = [
    "ShellPermissionRule",
    "has_wildcards",
    "match_wildcard_pattern",
    "parse_permission_rule",
    "permission_rule_extract_prefix",
]
