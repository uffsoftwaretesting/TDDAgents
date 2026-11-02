"""
Whether git is available, checked once per session.

Ported from `reference/claude-code/src/utils/plugins/gitAvailability.ts` -> `isCommandAvailable`,
`checkGitAvailable` (memoized), `markGitUnavailable`, `clearGitAvailabilityCache`.

Only PATH is checked (`which`), never an exec, as upstream. `mark_git_unavailable` pins the answer
to False for the rest of the session (a git on PATH that cannot run, such as the macOS xcrun shim).
"""

from __future__ import annotations

import shutil

_cached: bool | None = None


async def is_command_available(command: str) -> bool:
    """`isCommandAvailable`: the command resolves on PATH, without running it."""
    try:
        return bool(shutil.which(command))
    except OSError:
        return False


async def check_git_available() -> bool:
    """`checkGitAvailable`, memoized for the session."""
    global _cached
    if _cached is None:
        _cached = await is_command_available("git")
    return _cached


def mark_git_unavailable() -> None:
    """`markGitUnavailable`."""
    global _cached
    _cached = False


def clear_git_availability_cache() -> None:
    """`clearGitAvailabilityCache` (tests)."""
    global _cached
    _cached = None


__all__ = ["check_git_available", "clear_git_availability_cache", "is_command_available", "mark_git_unavailable"]
