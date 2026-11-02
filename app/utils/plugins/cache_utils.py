"""
Plugin cache maintenance: clearing the in-memory caches, and garbage-collecting orphaned plugin
versions on disk.

Ported from `reference/claude-code/src/utils/plugins/cacheUtils.ts` -> `ORPHANED_AT_FILENAME`,
`CLEANUP_AGE_MS`, `clearAllPluginCaches`, `clearAllCaches`, `markPluginVersionOrphaned`,
`cleanupOrphanedPluginVersionsInBackground`, `getOrphanedAtPath`, `removeOrphanedAtMarker`,
`getInstalledVersionPaths`, `processOrphanedPluginVersion`, `removeIfEmpty`, `readSubdirs`.

An updated or uninstalled version gets a `.orphaned_at` marker and is deleted once the marker
is older than seven days (concurrent sessions may still use it); a version installed again
loses its marker.

`clear_all_plugin_caches` clears the loader's memo (`clearPluginCache`), the plugin commands,
skills (`clearCommandsCache`'s plugin half), agents and hooks memos, starts the background
`pruneRemovedPluginHooks`, and clears the plugin options. Not ported: `clearPluginOutputStyleCache`
and `clearAllOutputStylesCache` (TDDAgents has no output styles), and `clear_all_caches`'
`clearAgentDefinitionsCache`, `clearPromptCache` (SkillTool) and `resetSentSkillNames`: those
have no process-wide state here (agent definitions are read from disk on each use; the skill
registry and its `sent_skill_names` live per run, and the run resets them when it refreshes its
plugins, `DevelopmentRun.refresh_plugins`).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from app.utils.plugins.installed_plugins_manager import load_installed_plugins_from_disk
from app.utils.plugins.load_plugin_agents import clear_plugin_agent_cache
from app.utils.plugins.load_plugin_commands import (
    clear_plugin_command_cache,
    clear_plugin_skills_cache,
)
from app.utils.plugins.load_plugin_hooks import (
    clear_plugin_hook_cache,
    prune_removed_plugin_hooks_in_background,
)
from app.utils.plugins.plugin_loader import clear_plugin_cache, get_plugin_cache_path
from app.utils.plugins.plugin_options_storage import clear_plugin_options_cache
from app.utils.plugins.zip_cache import is_plugin_zip_cache_enabled

logger = logging.getLogger("TDDOrchestrator.Plugins")

ORPHANED_AT_FILENAME = ".orphaned_at"
#: `CLEANUP_AGE_MS`: seven days.
CLEANUP_AGE_MS = 7 * 24 * 60 * 60 * 1000


def clear_all_plugin_caches() -> None:
    """`clearAllPluginCaches`."""
    clear_plugin_cache()
    clear_plugin_command_cache()
    clear_plugin_agent_cache()
    clear_plugin_hook_cache()
    # Prune-only and fire-and-forget: disabled plugins' hooks stop at once, new ones wait for a reload.
    prune_removed_plugin_hooks_in_background()
    clear_plugin_options_cache()


def clear_all_caches() -> None:
    """`clearAllCaches`: the plugin caches, then `clearCommandsCache`'s plugin skills."""
    clear_all_plugin_caches()
    clear_plugin_skills_cache()


def _now_ms() -> float:
    return time.time() * 1000


async def mark_plugin_version_orphaned(version_path: str) -> None:
    """`markPluginVersionOrphaned`: write the marker (the current time in ms); failures are logged."""
    try:
        Path(_get_orphaned_at_path(version_path)).write_text(str(int(_now_ms())), encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to write .orphaned_at: %s: %s", version_path, exc)


def _get_orphaned_at_path(version_path: str) -> str:
    return os.path.join(version_path, ORPHANED_AT_FILENAME)


async def _remove_orphaned_at_marker(version_path: str) -> None:
    try:
        os.unlink(_get_orphaned_at_path(version_path))
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.debug("Failed to remove .orphaned_at: %s: %s", version_path, exc)


def _get_installed_version_paths() -> set[str] | None:
    try:
        data = load_installed_plugins_from_disk()
        return {e["installPath"] for entries in data["plugins"].values() for e in entries}
    except Exception as exc:  # noqa: BLE001 - upstream skips the cleanup when the registry cannot load
        logger.debug("Failed to load installed plugins: %s", exc)
        return None


async def _process_orphaned_plugin_version(version_path: str, now: float) -> None:
    marker = _get_orphaned_at_path(version_path)
    try:
        orphaned_at = os.stat(marker).st_mtime * 1000
    except FileNotFoundError:
        await mark_plugin_version_orphaned(version_path)
        return
    except OSError as exc:
        logger.debug("Failed to stat orphaned marker: %s: %s", version_path, exc)
        return
    if now - orphaned_at > CLEANUP_AGE_MS:
        try:
            shutil.rmtree(version_path)
        except OSError as exc:
            logger.debug("Failed to delete orphaned version: %s: %s", version_path, exc)


async def _read_subdirs(dir_path: str) -> list[str]:
    try:
        with os.scandir(dir_path) as iterator:
            return [e.name for e in iterator if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


async def _remove_if_empty(dir_path: str) -> None:
    if not await _read_subdirs(dir_path):
        try:
            shutil.rmtree(dir_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("Failed to remove empty dir: %s: %s", dir_path, exc)


async def cleanup_orphaned_plugin_versions_in_background() -> None:
    """
    `cleanupOrphanedPluginVersionsInBackground`: clear the markers of installed versions, then
    mark (or, after seven days, delete) every cached version not installed, and remove plugin
    and marketplace directories left without subdirectories. Skipped in zip-cache mode.
    """
    if is_plugin_zip_cache_enabled():
        return
    try:
        installed = _get_installed_version_paths()
        if installed is None:
            return
        cache_path = get_plugin_cache_path()
        now = _now_ms()
        for path in installed:
            await _remove_orphaned_at_marker(path)
        for marketplace in await _read_subdirs(cache_path):
            marketplace_path = os.path.join(cache_path, marketplace)
            for plugin in await _read_subdirs(marketplace_path):
                plugin_path = os.path.join(marketplace_path, plugin)
                for version in await _read_subdirs(plugin_path):
                    version_path = os.path.join(plugin_path, version)
                    if version_path in installed:
                        continue
                    await _process_orphaned_plugin_version(version_path, now)
                await _remove_if_empty(plugin_path)
            await _remove_if_empty(marketplace_path)
    except Exception as exc:  # noqa: BLE001 - upstream's cleanup is best-effort
        logger.debug("Plugin cache cleanup failed: %s", exc)


__all__ = [
    "CLEANUP_AGE_MS",
    "ORPHANED_AT_FILENAME",
    "cleanup_orphaned_plugin_versions_in_background",
    "clear_all_caches",
    "clear_all_plugin_caches",
    "mark_plugin_version_orphaned",
]
