"""
Skill argument substitution (Phase 8).

Ported from `reference/claude-code/src/utils/argumentSubstitution.ts` -> `parseArguments`,
`parseArgumentNames`, `substituteArguments`. `parseArguments` there tokenizes with the shell
parser and falls back to whitespace splitting when the input does not parse; `shlex.split` is
the same contract here.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Sequence
from typing import Any

_INDEXED_ARGUMENT = re.compile(r"\$ARGUMENTS\[(\d+)\]")
_POSITIONAL_ARGUMENT = re.compile(r"\$(\d+)(?!\w)")


def parse_arguments(args: str) -> list[str]:
    """
    `parseArguments`: shell-like tokens, or whitespace-split when the quoting is broken. Upstream's
    blank-input guard is implicit: both splits return `[]` for blank input.
    """
    try:
        return shlex.split(args)
    except ValueError:
        return args.split()


def parse_argument_names(argument_names: Any) -> list[str]:
    """`parseArgumentNames`: names from a string or list; numeric and blank names are dropped."""

    def is_valid(name: Any) -> bool:
        return isinstance(name, str) and name.strip() != "" and not name.isdigit()

    if not argument_names:
        return []
    if isinstance(argument_names, list):
        return [name for name in argument_names if is_valid(name)]
    if isinstance(argument_names, str):
        return [name for name in argument_names.split() if is_valid(name)]
    return []


def _literal(value: str) -> Callable[[re.Match[str]], str]:
    """A `re.sub` replacement that inserts `value` as is (no backslash or group expansion)."""
    return lambda _match: value


def substitute_arguments(
    content: str,
    args: str | None,
    append_if_no_placeholder: bool = True,
    argument_names: Sequence[str] = (),
) -> str:
    """
    `substituteArguments`: named (`$name`), indexed (`$ARGUMENTS[0]`), positional (`$0`) and
    whole (`$ARGUMENTS`) placeholders; when nothing matched and args were given, they are
    appended as `ARGUMENTS: ...` so the skill still sees them.
    """
    if args is None:
        return content
    parsed = parse_arguments(args)
    original = content

    def at(index: int) -> str:
        return parsed[index] if index < len(parsed) else ""

    for i, name in enumerate(argument_names):
        if not name:
            continue
        content = re.sub(rf"\${re.escape(name)}(?![\[\w])", _literal(at(i)), content)
    content = _INDEXED_ARGUMENT.sub(lambda m: at(int(m.group(1))), content)
    content = _POSITIONAL_ARGUMENT.sub(lambda m: at(int(m.group(1))), content)
    content = content.replace("$ARGUMENTS", args)
    if content == original and append_if_no_placeholder and args:
        content = f"{content}\n\nARGUMENTS: {args}"
    return content


__all__ = ["parse_argument_names", "parse_arguments", "substitute_arguments"]
