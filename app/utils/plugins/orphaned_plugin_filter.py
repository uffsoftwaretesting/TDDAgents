"""
Ripgrep exclusions for orphaned plugin versions, so Grep/Glob never return files from a version
kept on disk only for its seven-day grace period.

Ported from `reference/claude-code/src/utils/plugins/orphanedPluginFilter.ts` -> `ORPHANED_AT_FILENAME`,
`getGlobExclusionsForPluginCache`, `clearPluginCacheExclusions`, `pathsOverlap`,
`normalizeForCompare`.

One `rg --files --hidden --no-ignore --max-depth 4 --glob .orphaned_at` call over the plugin
cache finds the markers; each becomes `!**/<marketplace>/<plugin>/<version>/**`. The list is
frozen for the session once computed (`clear_plugin_cache_exclusions` resets it, upstream's
`/reload-plugins`). Best-effort: any failure is an empty list.

Divergence: upstream's `ripGrep` runs in the session; here rg (the vendored binary,
`builtin_ripgrep_path`, else `rg` on PATH) runs on the host directly, since the plugin cache is
a host directory and not part of any workspace.
"""

from __future__ import annotations

import os
import sys

from app.utils.exec_file_no_throw import exec_file_no_throw_with_cwd
from app.utils.plugins.plugin_directories import get_plugins_directory
from app.utils.ripgrep import builtin_ripgrep_path

#: Inlined from cache_utils, as upstream does (avoids an import cycle).
ORPHANED_AT_FILENAME = ".orphaned_at"

_cached_exclusions: list[str] | None = None


def _ripgrep() -> str:
    builtin = builtin_ripgrep_path()
    return builtin if os.path.exists(builtin) else "rg"


async def get_glob_exclusions_for_plugin_cache(search_path: str | None = None) -> list[str]:
    """`getGlobExclusionsForPluginCache`: `[]` when `search_path` does not overlap the cache."""
    global _cached_exclusions
    cache_path = os.path.normpath(os.path.join(get_plugins_directory(), "cache"))
    if search_path and not _paths_overlap(search_path, cache_path):
        return []
    if _cached_exclusions is not None:
        return _cached_exclusions
    try:
        result = await exec_file_no_throw_with_cwd(
            _ripgrep(),
            ["--files", "--hidden", "--no-ignore", "--max-depth", "4", "--glob", ORPHANED_AT_FILENAME, cache_path],
            cwd=cache_path,
        )
        # `ripGrep`: exit 1 is "no matches"; any other failure keeps whatever output there was.
        markers = [line.removesuffix("\r") for line in result.stdout.strip().split("\n")]
        exclusions: list[str] = []
        for marker in (line for line in markers if line):
            version_dir = os.path.dirname(marker)
            rel = os.path.relpath(version_dir, cache_path) if os.path.isabs(version_dir) else version_dir
            exclusions.append(f"!**/{rel.replace(chr(92), '/')}/**")
        _cached_exclusions = exclusions
    except Exception:  # noqa: BLE001 - best-effort: never break the search tools
        _cached_exclusions = []
    return _cached_exclusions


def clear_plugin_cache_exclusions() -> None:
    """`clearPluginCacheExclusions`."""
    global _cached_exclusions
    _cached_exclusions = None


def _normalize_for_compare(path: str) -> str:
    normalized = os.path.normpath(path)
    return normalized.lower() if sys.platform == "win32" else normalized


def _paths_overlap(a: str, b: str) -> bool:
    """`pathsOverlap`: one path is a prefix of the other (the root overlaps everything)."""
    na, nb = _normalize_for_compare(a), _normalize_for_compare(b)
    return (
        na == nb
        or na == os.sep
        or nb == os.sep
        or na.startswith(nb + os.sep)
        or nb.startswith(na + os.sep)
    )


__all__ = ["ORPHANED_AT_FILENAME", "clear_plugin_cache_exclusions", "get_glob_exclusions_for_plugin_cache"]
