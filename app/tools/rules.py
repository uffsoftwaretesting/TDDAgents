"""
Permission-rule strings: `ToolName` or `ToolName(content)`.

One parser, three callers. Phase 1B needs it twice already — `resolve_tools` reads the
tool name out of an agent's `tools:` entry, and the hook dispatcher reads the `if:`
condition that decides whether a hook is worth spawning a process for. Phase 5 needs the
same syntax a third time for `Agent(researcher,refactorer)` target scoping.

Ported from claude-code's `permissionRuleParser.ts` rather than reinvented, because the
edge cases are already settled there and they are the kind that surface late:

    Bash                      -> ("Bash", None)
    Bash()                    -> ("Bash", None)      empty content is a tool-wide rule
    Bash(*)                   -> ("Bash", None)      so is a standalone wildcard
    Bash(pip *)               -> ("Bash", "pip *")
    Bash(python -c "f\\(1\\)")  -> ("Bash", 'python -c "f(1)"')
    (foo)                     -> ("(foo)", None)     no tool name: not a rule at all
    Bash(a) trailing          -> ("Bash(a) trailing", None)

Anything malformed degrades to "the whole string is a tool name" instead of raising. A
typo in a settings file should cost one non-matching rule, never the run.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionRule:
    """A parsed `ToolName(content)` string."""

    tool_name: str
    rule_content: str | None = None


def _is_escaped(text: str, index: int) -> bool:
    """True when the character at `index` is preceded by an odd number of backslashes."""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_first_unescaped(text: str, char: str) -> int:
    for index, current in enumerate(text):
        if current == char and not _is_escaped(text, index):
            return index
    return -1


def _find_last_unescaped(text: str, char: str) -> int:
    for index in range(len(text) - 1, -1, -1):
        if text[index] == char and not _is_escaped(text, index):
            return index
    return -1


def escape_rule_content(content: str) -> str:
    """
    Escapes content for storage inside `ToolName(...)`.

    Order matters: backslashes first, then parentheses. Reversing it would double-unescape
    on the way back out.
    """
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def unescape_rule_content(content: str) -> str:
    """Reverses `escape_rule_content`. Order is the exact mirror: parens first, backslashes last."""
    return content.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def parse_rule(rule_string: str) -> PermissionRule:
    """
    Splits a rule string into its tool name and optional content.

    Never raises. A string that is not a well-formed rule is returned whole as the tool
    name, which simply matches nothing.
    """
    open_index = _find_first_unescaped(rule_string, "(")
    if open_index == -1:
        return PermissionRule(tool_name=rule_string)

    close_index = _find_last_unescaped(rule_string, ")")
    if close_index <= open_index:
        return PermissionRule(tool_name=rule_string)

    # Trailing text after the closing paren means this was never a rule.
    if close_index != len(rule_string) - 1:
        return PermissionRule(tool_name=rule_string)

    tool_name = rule_string[:open_index]
    raw_content = rule_string[open_index + 1:close_index]

    if not tool_name:
        return PermissionRule(tool_name=rule_string)

    # "Bash()" and "Bash(*)" both mean "the Bash tool, unconditionally".
    if raw_content in ("", "*"):
        return PermissionRule(tool_name=tool_name)

    return PermissionRule(tool_name=tool_name, rule_content=unescape_rule_content(raw_content))


def split_targets(rule_content: str | None) -> list[str]:
    """
    Splits a rule's content on commas, for specs that carry a list.

    `Agent(researcher, refactorer)` -> ["researcher", "refactorer"]. Phase 5's target
    scoping is the only consumer, but the syntax belongs with the parser.
    """
    if not rule_content:
        return []
    return [part.strip() for part in rule_content.split(",") if part.strip()]


def rule_matches(rule_string: str, tool_name: str, command: str | None = None) -> bool:
    """
    Decides whether a rule applies to one tool call.

    A rule with no content matches the tool outright. A rule with content matches when
    `command` either equals the content, starts with it as a whitespace-delimited prefix,
    or satisfies it as a glob — so `Bash(pip *)` catches `pip install x`, and
    `Bash(git commit)` catches `git commit -m "x"` without catching `git commit-tree`.
    """
    rule = parse_rule(rule_string)
    if rule.tool_name != tool_name:
        return False
    if rule.rule_content is None:
        return True
    if command is None:
        return False

    content = rule.rule_content
    if command == content or command.startswith(content + " "):
        return True
    return fnmatch.fnmatchcase(command, content)
