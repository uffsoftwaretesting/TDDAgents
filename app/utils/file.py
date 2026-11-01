"""
File helpers.

Ported from `reference/claude-code/src/utils/file.ts` -> `FILE_NOT_FOUND_CWD_NOTE`,
`addLineNumbers` (the compact `N<TAB>line` form), `getAbsoluteAndRelativePaths`,
`getDisplayPath`, `stripLineNumberPrefix`. The working directory is the context's workspace's tracked one (`getCwd`
is process-wide upstream); without a context it is the process's.
"""

from __future__ import annotations

import os
import posixpath
import re
from typing import Any

#: `src/utils/file.ts` -> `FILE_NOT_FOUND_CWD_NOTE`; our cwd is the workspace root.
FILE_NOT_FOUND_CWD_NOTE = "Note: your current working directory is the workspace root."


def add_line_numbers(lines: list[str], start_line: int) -> str:
    """`src/utils/file.ts` -> `addLineNumbers`, compact (`N<TAB>line`) form."""
    return "\n".join(f"{start_line + i}\t{line}" for i, line in enumerate(lines))


def get_absolute_and_relative_paths(file_path: str | None, context: Any = None) -> tuple[str | None, str | None]:
    """`getAbsoluteAndRelativePaths`: `file_path` expanded, and relative to the working directory."""
    if not file_path:
        return None, None
    from app.utils.cwd import get_cwd, get_original_cwd_for
    from app.utils.path import expand_path

    expanded = os.path.expanduser(file_path) if file_path.startswith("~") else expand_path(file_path, context)
    root = get_original_cwd_for(context)
    absolute = expanded if posixpath.isabs(expanded) else posixpath.normpath(posixpath.join(root, expanded))
    relative = posixpath.relpath(absolute, get_cwd(context))
    return absolute, ("" if relative == "." else relative)


def get_display_path(file_path: str, context: Any = None) -> str:
    """`getDisplayPath`: relative inside the working directory, `~/…` under home, else absolute."""
    _, relative = get_absolute_and_relative_paths(file_path, context)
    if relative and not relative.startswith(".."):
        return relative
    home = os.path.expanduser("~")
    if file_path.startswith(home + os.sep):
        return "~" + file_path[len(home):]
    return file_path


_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+[→\t](.*)$", re.DOTALL)


def strip_line_number_prefix(line: str) -> str:
    """`stripLineNumberPrefix`: `N<tab>text` (or `N→text`) becomes `text`; anything else is kept."""
    match = _LINE_NUMBER_PREFIX.match(line)
    return match.group(1) if match else line
