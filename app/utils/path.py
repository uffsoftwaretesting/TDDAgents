"""
A tool's relative path, resolved against the shell's working directory.

Ported from `reference/claude-code/src/utils/path.ts` -> `expandPath` (a relative path joins
`getCwd()`), for the file tools' `backfillObservableInput` and calls. TDDAgents' workspaces
resolve a relative path against the project root, so the result keeps that form: while the
tracked directory is the root the path is unchanged; after a `cd` into `sub` it becomes
`sub/<path>`, still relative to the root; a tracked directory outside the root (an additional
working directory) gives an absolute path. An absolute path, or one starting with `~`, is left
as it is: the tools and the permission checks already resolve those.
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Callable
from typing import Any

from app.utils.cwd import get_cwd, get_original_cwd_for


def tracked_cwd_offset(context: Any) -> str | None:
    """
    Where the tracked directory is, as the workspace resolves paths: `None` while it is the
    original one, a root-relative directory inside the root, or an absolute one outside it.
    """
    cwd, original = get_cwd(context), get_original_cwd_for(context)
    if os.path.normpath(cwd) == os.path.normpath(original):
        return None
    relative = os.path.relpath(cwd, original)
    return cwd if relative.startswith("..") else relative


def expand_path(raw: str, context: Any) -> str:
    """`expandPath`: a relative `raw` against the tracked working directory (see the module)."""
    if not raw or os.path.isabs(raw) or raw.startswith("~"):
        return raw
    offset = tracked_cwd_offset(context)
    if offset is None:
        return raw
    return posixpath.normpath(posixpath.join(offset, raw))


def backfill_path(key: str, *, default_to_cwd: bool = False) -> Callable[[dict[str, Any], Any], None]:
    """
    A file tool's `backfillObservableInput`: `input[key]` through `expand_path`. With
    `default_to_cwd` (Glob, Grep: `path ?? getCwd()`), an absent path becomes the tracked
    directory once a command has moved it.
    """

    def backfill(input: dict[str, Any], context: Any) -> None:
        value = input.get(key)
        if isinstance(value, str) and value:
            input[key] = expand_path(value, context)
        elif default_to_cwd and value is None:
            offset = tracked_cwd_offset(context)
            if offset is not None:
                input[key] = offset

    return backfill
