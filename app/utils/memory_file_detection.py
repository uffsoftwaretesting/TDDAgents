"""
Whether a path is an auto-memory file, for this context's project.

Ported from `reference/claude-code/src/utils/memoryFileDetection.ts` -> `isAutoMemFile` (auto
memory on, and the path in its directory), `detectSessionFileType`, `detectSessionPatternType`,
`isAutoManagedMemoryFile`, `isMemoryDirectory`, `isShellCommandTargetingMemory`,
`isAutoManagedMemoryPattern` (the REPL's read/search collapse counts memory operations apart).
The settings the decision reads (`autoMemoryEnabled`, `autoMemoryDirectory`) are the project's
own, loaded from the workspace root. The config home is TDDAgents' (`config_home`); paths are
POSIX, so upstream's Windows and MinGW conversions have nothing to do.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.memdir.paths import (
    get_auto_mem_path,
    get_memory_base_dir,
    is_auto_mem_path,
    is_auto_memory_enabled,
)
from app.memdir.team_mem_paths import (
    is_team_mem_file,
    is_team_mem_path,
    is_team_memory_enabled,
)
from app.utils.cwd import get_original_cwd_for
from app.utils.settings.settings import SettingsLocations, load_settings


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """The project root, its settings locations and merged settings, as memdir reads them."""

    project_root: str
    locations: SettingsLocations
    settings: dict[str, Any]


def memory_context(context: Any) -> MemoryContext:
    """The memdir inputs for `context`'s project."""
    root = get_original_cwd_for(context)
    locations = SettingsLocations(project_root=Path(root))
    return MemoryContext(root, locations, load_settings(locations))


def auto_memory_dir(context: Any) -> str | None:
    """The auto-memory directory when auto memory is on, else `None`."""
    memory = memory_context(context)
    if not is_auto_memory_enabled(settings=memory.settings):
        return None
    return get_auto_mem_path(memory.project_root, memory.locations)


def auto_memory_target(context: Any, raw_path: str) -> str | None:
    """
    `isAutoMemFile`: the normalized host path when `raw_path` is absolute and names a file in the
    auto-memory directory (auto memory on), else `None`. As with agent memory, only an absolute
    path counts: the prompt names the directory absolutely, and a relative path is the project's.
    """
    expanded = os.path.expanduser(str(raw_path or ""))
    if not posixpath.isabs(expanded):
        return None
    memory = memory_context(context)
    if not is_auto_memory_enabled(settings=memory.settings):
        return None
    normalized = posixpath.normpath(expanded)
    return normalized if is_auto_mem_path(normalized, memory.project_root, memory.locations) else None


#: `SessionFileType`.
SESSION_MEMORY = "session_memory"
SESSION_TRANSCRIPT = "session_transcript"

_ABSOLUTE_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:[/\\]|/)[^\s'\"]+")
_TRAILING_SHELL_METACHARACTERS = re.compile(r"[,;|&>]+$")


def _config_dir() -> str:
    from app.utils.tool_result_storage import config_home

    return str(config_home())


def detect_session_file_type(file_path: str) -> str | None:
    """`detectSessionFileType`: a session memory file or transcript under the config home."""
    if not file_path.startswith(_config_dir()):
        return None
    if "/session-memory/" in file_path and file_path.endswith(".md"):
        return SESSION_MEMORY
    if "/projects/" in file_path and file_path.endswith(".jsonl"):
        return SESSION_TRANSCRIPT
    return None


def detect_session_pattern_type(pattern: str) -> str | None:
    """`detectSessionPatternType`: a glob that reaches for session memory or transcripts."""
    normalized = pattern.replace("\\", "/")
    if "session-memory" in normalized and (".md" in normalized or normalized.endswith("*")):
        return SESSION_MEMORY
    if ".jsonl" in normalized:
        return SESSION_TRANSCRIPT
    return None


def _agent_memory_file(context: Any, file_path: str) -> bool:
    """`isAgentMemFile`: auto memory on, and the path in this run's agent memory."""
    store = getattr(context, "agent_memory_store", None)
    if store is None or not is_auto_memory_enabled(settings=memory_context(context).settings):
        return False
    return bool(store.is_memory_path(posixpath.normpath(file_path), getattr(context, "run_id", None) or None))


def _team_memory_file(context: Any, file_path: str) -> bool:
    memory = memory_context(context)
    return is_team_mem_file(file_path, memory.project_root, memory.locations, settings=memory.settings)


def is_auto_managed_memory_file(context: Any, file_path: str) -> bool:
    """
    `isAutoManagedMemoryFile`: auto memory, team memory, session memory or a transcript, or agent
    memory; never a user-managed instruction file (`TDDAGENTS.md`, rules).
    """
    return (auto_memory_target(context, file_path) is not None or _team_memory_file(context, file_path)
            or detect_session_file_type(file_path) is not None or _agent_memory_file(context, file_path))


def is_memory_directory(context: Any, dir_path: str) -> bool:
    """`isMemoryDirectory`: a directory Grep/Glob search that holds memory."""
    normalized = posixpath.normpath(dir_path)
    memory = memory_context(context)
    enabled = is_auto_memory_enabled(settings=memory.settings)
    if enabled and ("/agent-memory/" in normalized or "/agent-memory-local/" in normalized):
        return True
    if is_team_memory_enabled(settings=memory.settings) and is_team_mem_path(
            normalized, memory.project_root, memory.locations):
        return True
    if enabled:
        auto_mem = get_auto_mem_path(memory.project_root, memory.locations)
        if normalized == auto_mem.rstrip("/") or normalized.startswith(auto_mem):
            return True
    under_config = normalized.startswith(_config_dir())
    under_memory_base = normalized.startswith(get_memory_base_dir())
    if not under_config and not under_memory_base:
        return False
    if "/session-memory/" in normalized:
        return True
    if under_config and "/projects/" in normalized:
        return True
    return enabled and "/memory/" in normalized


def is_shell_command_targeting_memory(context: Any, command: str) -> bool:
    """`isShellCommandTargetingMemory`: a command naming an absolute path that is memory."""
    memory = memory_context(context)
    auto_mem = (get_auto_mem_path(memory.project_root, memory.locations).rstrip("/")
                if is_auto_memory_enabled(settings=memory.settings) else "")
    if not any(d and d in command for d in (_config_dir(), get_memory_base_dir(), auto_mem)):
        return False
    for token in _ABSOLUTE_PATH_TOKEN.findall(command):
        path = _TRAILING_SHELL_METACHARACTERS.sub("", token)
        if is_auto_managed_memory_file(context, path) or is_memory_directory(context, path):
            return True
    return False


def is_auto_managed_memory_pattern(context: Any, pattern: str) -> bool:
    """`isAutoManagedMemoryPattern`: a glob for session files, or for agent memory (auto memory on)."""
    if detect_session_pattern_type(pattern) is not None:
        return True
    normalized = pattern.replace("\\", "/")
    return is_auto_memory_enabled(settings=memory_context(context).settings) and (
        "agent-memory/" in normalized or "agent-memory-local/" in normalized)
