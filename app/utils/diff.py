"""
The patch a file dialog shows.

Ported from `reference/claude-code/src/utils/diff.ts` -> `CONTEXT_LINES`, `getPatchForDisplay`,
`getPatchFromContents`, and `utils/file.ts` -> `convertLeadingTabsToSpaces`. Upstream returns the
`diff` package's `structuredPatch` hunks (`StructuredPatchHunk`: `oldStart`, `oldLines`,
`newStart`, `newLines`, `lines`); `get_structured_patch_for_display` /
`get_structured_patch_from_contents` return those, as a tool's structured output carries them.
The dialogs print a hunk as its unified-diff text (`@@ -a,b +c,d @@` and the `' '`/`-`/`+` lines,
`format_hunk`). The escaping of `&`/`$` around the `diff` package's string replace is not needed:
Python's `str.replace` takes the replacement literally.

The lines are the `diff` package's line tokens (2026-11-14): each line keeps its newline while the
lines are compared, so a final newline added or removed is a changed last line, and a hunk line
whose text has no newline (a file's last line without one) is followed by the package's
`\\ No newline at end of file` marker. A side with no lines in a hunk reads `start-1,0` in its
header, as `formatPatch` and `difflib` write it.

*Divergence:* which lines pair up comes from `difflib`'s matcher, not the package's Myers diff, so
two equally short patches can differ in where a change is placed.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: `diff.ts` -> `CONTEXT_LINES`.
CONTEXT_LINES = 3


@dataclass(frozen=True, slots=True)
class FileEdit:
    """One `old_string` -> `new_string` replacement (`FileEdit` in `diff.ts`)."""

    old_string: str
    new_string: str
    replace_all: bool = False


def convert_leading_tabs_to_spaces(content: str) -> str:
    """`convertLeadingTabsToSpaces`: each leading tab becomes two spaces."""
    if "\t" not in content:
        return content
    lines = []
    for line in content.split("\n"):
        stripped = line.lstrip("\t")
        lines.append("  " * (len(line) - len(stripped)) + stripped)
    return "\n".join(lines)


#: The `diff` package's marker after a line that has no newline.
NO_NEWLINE_MARKER = "\\ No newline at end of file"
_LINE_TOKEN = re.compile(r"[^\n]*\n|[^\n]+$")


def split_lines(text: str) -> list[str]:
    """The `diff` package's line tokens: each line with its `\\n`, the last one without it when it has none."""
    return _LINE_TOKEN.findall(text)


def _hunk_lines(sigil: str, tokens: Sequence[str]) -> list[str]:
    """`structuredPatch`'s step 2: each token's trailing newline dropped, or the marker after it."""
    lines: list[str] = []
    for token in tokens:
        if token.endswith("\n"):
            lines.append(sigil + token[:-1])
        else:
            lines += [sigil + token, NO_NEWLINE_MARKER]
    return lines


def _structured_hunks(before: str, after: str) -> list[dict[str, Any]]:
    """`structuredPatch(...).hunks` with `CONTEXT_LINES` of context, as `difflib.unified_diff` groups them."""
    a, b = split_lines(before), split_lines(after)
    hunks: list[dict[str, Any]] = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(CONTEXT_LINES):
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += _hunk_lines(" ", a[i1:i2])
                continue
            lines += _hunk_lines("-", a[i1:i2])  # empty for an insert
            lines += _hunk_lines("+", b[j1:j2])  # empty for a delete
        first, last = group[0], group[-1]
        hunks.append({"oldStart": first[1] + 1, "oldLines": last[2] - first[1],
                      "newStart": first[3] + 1, "newLines": last[4] - first[3], "lines": lines})
    return hunks


def _unified_range(start: int, length: int) -> str:
    """A hunk header's range as `difflib` writes it: `start` alone for one line, `start-1,0` for none."""
    if length == 0:
        return f"{start - 1},0"
    return str(start) if length == 1 else f"{start},{length}"


def format_hunk(hunk: Mapping[str, Any]) -> str:
    """A structured hunk as unified-diff text: its `@@ -a,b +c,d @@` header, then its lines."""
    header = (f"@@ -{_unified_range(hunk['oldStart'], hunk['oldLines'])} "
              f"+{_unified_range(hunk['newStart'], hunk['newLines'])} @@")
    return "\n".join([header, *hunk["lines"]])


def get_structured_patch_for_display(file_contents: str, edits: Sequence[FileEdit]) -> list[dict[str, Any]]:
    """`getPatchForDisplay`: the structured hunks the edits make to `file_contents`."""
    before = convert_leading_tabs_to_spaces(file_contents)
    after = before
    for edit in edits:
        old = convert_leading_tabs_to_spaces(edit.old_string)
        new = convert_leading_tabs_to_spaces(edit.new_string)
        after = after.replace(old, new) if edit.replace_all else after.replace(old, new, 1)
    return _structured_hunks(before, after)


def get_structured_patch_from_contents(old_content: str, new_content: str) -> list[dict[str, Any]]:
    """`getPatchFromContents`: the structured hunks between two whole contents, tabs kept (as upstream)."""
    return _structured_hunks(old_content, new_content)


def get_patch_for_display(file_contents: str, edits: Sequence[FileEdit]) -> list[str]:
    """`getPatchForDisplay`, each hunk as its unified-diff text."""
    return [format_hunk(hunk) for hunk in get_structured_patch_for_display(file_contents, edits)]


def get_patch_from_contents(old_content: str, new_content: str) -> list[str]:
    """`getPatchFromContents`, each hunk as its unified-diff text."""
    return [format_hunk(hunk) for hunk in get_structured_patch_from_contents(old_content, new_content)]


def render_hunks(hunks: Iterable[str]) -> str:
    """The hunks one after the other, as the dialog prints them."""
    return "\n".join(hunks)
