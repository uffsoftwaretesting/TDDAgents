"""
Team memory: a shared directory beside the private one, and the checks that keep writes in it.

Ported from `reference/claude-code/src/memdir/teamMemPaths.ts` -> `PathTraversalError`,
`sanitizePathKey`, `isTeamMemoryEnabled`, `getTeamMemPath`, `getTeamMemEntrypoint`,
`realpathDeepestExisting`, `isRealPathWithinTeamDir`, `isTeamMemPath`, `validateTeamMemWritePath`,
`validateTeamMemKey`, `isTeamMemFile`.

Upstream gates team memory on the compile-time `TEAMMEM` flag and the remote `tengu_herring_clock`
flag (default off); here it is the env opt-in `TDDAGENTS_TEAM_MEMORY`, on top of auto memory.
The team-sync service that writes into it is not ported, so the directory is only what the
model writes there itself.
"""

from __future__ import annotations

import errno
import os
import unicodedata
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

from app.memdir.paths import get_auto_mem_path, is_auto_memory_enabled
from app.tools.bash_tool.bash_security import is_env_truthy
from app.utils.settings.settings import SettingsLocations

TEAM_MEMORY_VAR = "TDDAGENTS_TEAM_MEMORY"


class PathTraversalError(Exception):
    """`PathTraversalError`: a team-memory path that escapes, or cannot be shown not to escape."""


def sanitize_path_key(key: str) -> str:
    """`sanitizePathKey`: refuse null bytes, encoded or Unicode-normalized traversal, `\\`, `/x`."""
    if "\0" in key:
        raise PathTraversalError(f'Null byte in path key: "{key}"')
    decoded = unquote(key)
    if decoded != key and (".." in decoded or "/" in decoded):
        raise PathTraversalError(f'URL-encoded traversal in path key: "{key}"')
    normalized = unicodedata.normalize("NFKC", key)
    if normalized != key and any(part in normalized for part in ("..", "/", "\\", "\0")):
        raise PathTraversalError(f'Unicode-normalized traversal in path key: "{key}"')
    if "\\" in key:
        raise PathTraversalError(f'Backslash in path key: "{key}"')
    if key.startswith("/"):
        raise PathTraversalError(f'Absolute path key: "{key}"')
    return key


def is_team_memory_enabled(env: Mapping[str, str] | None = None, settings: Mapping[str, Any] | None = None) -> bool:
    """`isTeamMemoryEnabled`: auto memory on, and the team opt-in set."""
    source = os.environ if env is None else env
    return is_auto_memory_enabled(env, settings) and is_env_truthy(source.get(TEAM_MEMORY_VAR))


def get_team_mem_path(project_root: str, locations: SettingsLocations | None = None) -> str:
    """`getTeamMemPath`: `<auto memory>/team/`."""
    return unicodedata.normalize("NFC", os.path.join(get_auto_mem_path(project_root, locations), "team") + os.sep)


def get_team_mem_entrypoint(project_root: str, locations: SettingsLocations | None = None) -> str:
    """`getTeamMemEntrypoint`."""
    return os.path.join(get_auto_mem_path(project_root, locations), "team", "MEMORY.md")


def realpath_deepest_existing(absolute_path: str) -> str:
    """
    `realpathDeepestExisting`: resolve the deepest ancestor that exists and re-attach the rest.
    A dangling symlink, a symlink loop or an unverifiable component raises.
    """
    tail: list[str] = []
    current = absolute_path
    parent = os.path.dirname(current)
    while current != parent:
        try:
            real = os.path.realpath(current, strict=True)
            return real if not tail else os.path.join(real, *reversed(tail))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                if os.path.islink(current):
                    raise PathTraversalError(
                        f'Dangling symlink detected (target does not exist): "{current}"') from None
            elif exc.errno == errno.ELOOP:
                raise PathTraversalError(f'Symlink loop detected in path: "{current}"') from None
            elif exc.errno not in (errno.ENOTDIR, errno.ENAMETOOLONG):
                raise PathTraversalError(
                    f'Cannot verify path containment ({errno.errorcode.get(exc.errno or 0, exc.errno)}): '
                    f'"{current}"') from None
            tail.append(current[len(parent) + len(os.sep):] if parent != os.sep else current[1:])
            current = parent
            parent = os.path.dirname(current)
    return absolute_path


def is_real_path_within_team_dir(real_candidate: str, team_dir: str) -> bool:
    """`isRealPathWithinTeamDir`: a team directory that does not exist yet contains nothing to escape."""
    try:
        real_team = os.path.realpath(team_dir.rstrip("/\\"), strict=True)
    except OSError as exc:
        return exc.errno in (errno.ENOENT, errno.ENOTDIR)
    return real_candidate == real_team or real_candidate.startswith(real_team + os.sep)


def is_team_mem_path(file_path: str, project_root: str, locations: SettingsLocations | None = None) -> bool:
    """`isTeamMemPath`: the resolved path lies in the team directory (lexically)."""
    return os.path.abspath(file_path).startswith(get_team_mem_path(project_root, locations))


def validate_team_mem_write_path(file_path: str, project_root: str,
                                 locations: SettingsLocations | None = None) -> str:
    """`validateTeamMemWritePath`: the resolved path, if it stays in the team directory, symlinks included."""
    if "\0" in file_path:
        raise PathTraversalError(f'Null byte in path: "{file_path}"')
    resolved = os.path.abspath(file_path)
    team_dir = get_team_mem_path(project_root, locations)
    if not resolved.startswith(team_dir):
        raise PathTraversalError(f'Path escapes team memory directory: "{file_path}"')
    if not is_real_path_within_team_dir(realpath_deepest_existing(resolved), team_dir):
        raise PathTraversalError(f'Path escapes team memory directory via symlink: "{file_path}"')
    return resolved


def validate_team_mem_key(relative_key: str, project_root: str, locations: SettingsLocations | None = None) -> str:
    """`validateTeamMemKey`: a server-supplied key, checked like a write path."""
    sanitize_path_key(relative_key)
    team_dir = get_team_mem_path(project_root, locations)
    resolved = os.path.abspath(os.path.join(team_dir, relative_key))
    if not resolved.startswith(team_dir):
        raise PathTraversalError(f'Key escapes team memory directory: "{relative_key}"')
    if not is_real_path_within_team_dir(realpath_deepest_existing(resolved), team_dir):
        raise PathTraversalError(f'Key escapes team memory directory via symlink: "{relative_key}"')
    return resolved


def is_team_mem_file(file_path: str, project_root: str, locations: SettingsLocations | None = None,
                     env: Mapping[str, str] | None = None, settings: Mapping[str, Any] | None = None) -> bool:
    """`isTeamMemFile`."""
    return is_team_memory_enabled(env, settings) and is_team_mem_path(file_path, project_root, locations)
