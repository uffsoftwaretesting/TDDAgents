"""
Git repository discovery and the git executable.

Ported from `reference/claude-code/src/utils/git.ts` -> `findGitRoot` (`findGitRootImpl`), `gitExe`.

Divergence: upstream memoizes `findGitRoot` in a 50-entry LRU; here each call walks the
filesystem, so a repository created mid-session is seen at once (no cache to clear).
"""

from __future__ import annotations

import os
import shutil
import unicodedata


def find_git_root(start_path: str) -> str | None:
    """`findGitRoot`: the nearest directory at or above `start_path` holding `.git` (a directory or a file)."""
    current = os.path.abspath(start_path)
    while True:
        git_path = os.path.join(current, ".git")
        if os.path.isdir(git_path) or os.path.isfile(git_path):
            return unicodedata.normalize("NFC", current)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def git_exe() -> str:
    """`gitExe`: the git binary on PATH, else plain `git`."""
    return shutil.which("git") or "git"


__all__ = ["find_git_root", "git_exe"]
