"""
The patch a file dialog shows.

Ported from `reference/claude-code/src/utils/diff.ts` -> `CONTEXT_LINES`, `getPatchForDisplay`,
`getPatchFromContents`, and `utils/file.ts` -> `convertLeadingTabsToSpaces`. Upstream returns
`structuredPatch` hunks that a React component draws; here a hunk is its unified-diff text
(`@@ -a,b +c,d @@` and the `' '`/`-`/`+` lines), which is what a line REPL can print. The
escaping of `&`/`$` around the `diff` package's string replace is not needed: Python's
`str.replace` takes the replacement literally.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

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


def _hunks(before: str, after: str) -> list[str]:
    diff = list(difflib.unified_diff(before.split("\n"), after.split("\n"), n=CONTEXT_LINES, lineterm=""))
    hunks: list[str] = []
    for line in diff[2:]:  # the `---` / `+++` file header
        if line.startswith("@@"):
            hunks.append(line)
        else:
            hunks[-1] += "\n" + line
    return hunks


def get_patch_for_display(file_contents: str, edits: Sequence[FileEdit]) -> list[str]:
    """`getPatchForDisplay`: the hunks the edits make to `file_contents`."""
    before = convert_leading_tabs_to_spaces(file_contents)
    after = before
    for edit in edits:
        old = convert_leading_tabs_to_spaces(edit.old_string)
        new = convert_leading_tabs_to_spaces(edit.new_string)
        after = after.replace(old, new) if edit.replace_all else after.replace(old, new, 1)
    return _hunks(before, after)


def get_patch_from_contents(old_content: str, new_content: str) -> list[str]:
    """`getPatchFromContents`: the hunks between two whole contents (an overwrite)."""
    return _hunks(convert_leading_tabs_to_spaces(old_content), convert_leading_tabs_to_spaces(new_content))


def render_hunks(hunks: Iterable[str]) -> str:
    """The hunks one after the other, as the dialog prints them."""
    return "\n".join(hunks)
