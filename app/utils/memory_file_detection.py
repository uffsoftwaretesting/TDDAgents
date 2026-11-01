"""
Whether a path is an auto-memory file, for this context's project.

Ported from `reference/claude-code/src/utils/memoryFileDetection.ts` -> `isAutoMemFile` (auto
memory on, and the path in its directory). The settings the decision reads (`autoMemoryEnabled`,
`autoMemoryDirectory`) are the project's own, loaded from the workspace root.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.memdir.paths import get_auto_mem_path, is_auto_mem_path, is_auto_memory_enabled
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
