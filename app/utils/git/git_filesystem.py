"""
Read git state straight from `.git/` without running git.

Ported from `reference/claude-code/src/utils/git/gitFilesystem.ts` -> `resolveGitDir`, `isSafeRefName`,
`isValidGitSha`, `readGitHead`, `resolveRef` (`resolveRefInDir`), `getCommonDir`, `getHeadForDir`.

Divergence: upstream memoizes `resolveGitDir` per start path (`clearResolveGitDirCache` for
tests); here every call reads the filesystem.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

from app.utils.git.git import find_git_root

_SAFE_REF = re.compile(r"^[a-zA-Z0-9/._+@-]+\Z", re.ASCII)
_SHA1 = re.compile(r"^[0-9a-f]{40}\Z", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z", re.ASCII)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


async def resolve_git_dir(start_path: str) -> str | None:
    """`resolveGitDir`: the real `.git` directory (a worktree's or submodule's `gitdir:` followed)."""
    root = find_git_root(os.path.abspath(start_path))
    if root is None:
        return None
    git_path = os.path.join(root, ".git")
    if os.path.isfile(git_path):
        content = (_read(git_path) or "").strip()
        if content.startswith("gitdir:"):
            raw = content[len("gitdir:"):].strip()
            return os.path.normpath(os.path.join(root, raw))
    if os.path.exists(git_path):
        return git_path
    return None


def is_safe_ref_name(name: str) -> bool:
    """`isSafeRefName`: no traversal, no leading `-` or `/`, no empty or `.` component, allowlisted characters."""
    if not name or name.startswith(("-", "/")):
        return False
    if ".." in name:
        return False
    if any(c in (".", "") for c in name.split("/")):
        return False
    return _SAFE_REF.search(name) is not None


def is_valid_git_sha(value: str) -> bool:
    """`isValidGitSha`: a full SHA-1 (40 hex) or SHA-256 (64 hex)."""
    return _SHA1.search(value) is not None or _SHA256.search(value) is not None


@dataclass(frozen=True)
class GitHead:
    """`readGitHead`'s result: on a branch (`name`) or detached (`sha`, possibly empty)."""

    type: Literal["branch", "detached"]
    name: str = ""
    sha: str = ""


async def read_git_head(git_dir: str) -> GitHead | None:
    """`readGitHead`: parse `HEAD` into a branch or a detached SHA; tampered content is `None`."""
    raw = _read(os.path.join(git_dir, "HEAD"))
    if raw is None:
        return None
    content = raw.strip()
    if content.startswith("ref:"):
        ref = content[len("ref:"):].strip()
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            if not is_safe_ref_name(name):
                return None
            return GitHead(type="branch", name=name)
        if not is_safe_ref_name(ref):
            return None
        sha = await resolve_ref(git_dir, ref)
        return GitHead(type="detached", sha=sha or "")
    if not is_valid_git_sha(content):
        return None
    return GitHead(type="detached", sha=content)


async def resolve_ref(git_dir: str, ref: str) -> str | None:
    """`resolveRef`: a loose ref, else packed-refs; then the worktree's common dir."""
    result = await _resolve_ref_in_dir(git_dir, ref)
    if result:
        return result
    common = await get_common_dir(git_dir)
    if common and common != git_dir:
        return await _resolve_ref_in_dir(common, ref)
    return None


async def _resolve_ref_in_dir(directory: str, ref: str) -> str | None:
    loose = _read(os.path.join(directory, ref))
    if loose is not None:
        content = loose.strip()
        if content.startswith("ref:"):
            target = content[len("ref:"):].strip()
            if not is_safe_ref_name(target):
                return None
            return await resolve_ref(directory, target)
        if not is_valid_git_sha(content):
            return None
        return content
    packed = _read(os.path.join(directory, "packed-refs"))
    if packed is None:
        return None
    for line in packed.split("\n"):
        if line.startswith(("#", "^")):
            continue
        space = line.find(" ")
        if space == -1:
            continue
        if line[space + 1:] == ref:
            sha = line[:space]
            return sha if is_valid_git_sha(sha) else None
    return None


async def get_common_dir(git_dir: str) -> str | None:
    """`getCommonDir`: the shared git directory a worktree's `commondir` names."""
    content = _read(os.path.join(git_dir, "commondir"))
    if content is None:
        return None
    return os.path.normpath(os.path.join(git_dir, content.strip()))


async def get_head_for_dir(cwd: str) -> str | None:
    """`getHeadForDir`: the HEAD commit SHA of the repository holding `cwd`, or `None`."""
    git_dir = await resolve_git_dir(cwd)
    if git_dir is None:
        return None
    head = await read_git_head(git_dir)
    if head is None:
        return None
    if head.type == "branch":
        return await resolve_ref(git_dir, f"refs/heads/{head.name}")
    return head.sha


__all__ = [
    "GitHead",
    "get_common_dir",
    "get_head_for_dir",
    "is_safe_ref_name",
    "is_valid_git_sha",
    "read_git_head",
    "resolve_git_dir",
    "resolve_ref",
]
