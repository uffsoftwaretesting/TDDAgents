"""
One diff hunk as numbered lines.

Ported from `reference/claude-code/src/components/StructuredDiff/Fallback.tsx` ->
`transformLinesToObjects`, `numberDiffLines` and `formatDiff`'s line layout: each line is its
number right-aligned in `maxWidth` columns (the widest number plus one), a space, its sigil (`+`,
`-` or a space) and its code. Lines are numbered from the hunk's `oldStart`: a kept or added line
takes the next number, and a run of removed lines is numbered on, then the count steps back so the
added lines that follow take the removed lines' numbers.

*Divergences:* the REPL prints lines, so the colours (added/removed backgrounds, dimmed kept
lines), the word-level highlighting (`processAdjacentLines`, `generateWordDiffElements`) and the
wrapping and padding to the terminal width are not ported; `StructuredDiff`'s syntax-highlighted
path (`ColorDiff`) falls back to this layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

LineType = Literal["add", "remove", "nochange"]


@dataclass(frozen=True, slots=True)
class DiffLine:
    """`DiffLine`: a hunk line's code (its sigil removed), its type and its line number."""

    code: str
    type: LineType
    i: int


def transform_lines_to_objects(lines: Sequence[str]) -> list[tuple[str, LineType]]:
    """`transformLinesToObjects`: each line's code after its first character, and its type by that character."""
    objects: list[tuple[str, LineType]] = []
    for line in lines:
        kind: LineType = "add" if line.startswith("+") else "remove" if line.startswith("-") else "nochange"
        objects.append((line[1:], kind))
    return objects


def number_diff_lines(diff: Sequence[tuple[str, LineType]], start_line: int) -> list[DiffLine]:
    """`numberDiffLines`: the line numbers, from `start_line`."""
    i = start_line
    result: list[DiffLine] = []
    queue = list(diff)
    while queue:
        code, kind = queue.pop(0)
        result.append(DiffLine(code, kind, i))
        if kind == "remove":
            removed = 0
            while queue and queue[0][1] == "remove":
                i += 1
                code, kind = queue.pop(0)
                result.append(DiffLine(code, kind, i))
                removed += 1
            i -= removed
        else:
            i += 1
    return result


def format_diff(lines: Sequence[str], starting_line_number: int) -> list[str]:
    """`formatDiff`: the hunk's lines, numbered, one string each."""
    numbered = number_diff_lines(transform_lines_to_objects(lines), starting_line_number)
    max_width = len(str(max((line.i for line in numbered), default=0))) + 1
    sigils = {"add": "+", "remove": "-", "nochange": " "}
    return [f"{str(line.i).rjust(max_width)} {sigils[line.type]}{line.code}" for line in numbered]


__all__ = ["DiffLine", "format_diff", "number_diff_lines", "transform_lines_to_objects"]
