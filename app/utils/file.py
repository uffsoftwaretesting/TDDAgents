"""
File helpers.

Ported from `reference/claude-code/src/utils/file.ts` -> `FILE_NOT_FOUND_CWD_NOTE`,
`addLineNumbers` (the compact `N<TAB>line` form).
"""

from __future__ import annotations

#: `src/utils/file.ts` -> `FILE_NOT_FOUND_CWD_NOTE`; our cwd is the workspace root.
FILE_NOT_FOUND_CWD_NOTE = "Note: your current working directory is the workspace root."


def add_line_numbers(lines: list[str], start_line: int) -> str:
    """`src/utils/file.ts` -> `addLineNumbers`, compact (`N<TAB>line`) form."""
    return "\n".join(f"{start_line + i}\t{line}" for i, line in enumerate(lines))
