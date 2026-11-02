"""
Where plugins live on disk: the plugins directory, the seed directories, per-plugin data.

Ported from `reference/claude-code/src/utils/plugins/pluginDirectories.ts` -> `PLUGINS_DIR`,
`COWORK_PLUGINS_DIR`, `getPluginsDirectoryName`, `getPluginsDirectory`, `getPluginSeedDirs`,
`sanitizePluginId`, `pluginDataDirPath`, `getPluginDataDir`, `getPluginDataDirSize`,
`deletePluginDataDir`.

Product substitutions: `~/.claude` is the config home (`TDDAGENTS_CONFIG_DIR`, default
`~/.tddagents`); `CLAUDE_CODE_PLUGIN_CACHE_DIR`, `CLAUDE_CODE_PLUGIN_SEED_DIR` and
`CLAUDE_CODE_USE_COWORK_PLUGINS` are `TDDAGENTS_PLUGIN_CACHE_DIR`, `TDDAGENTS_PLUGIN_SEED_DIR` and
`TDDAGENTS_USE_COWORK_PLUGINS`. Upstream's `--cowork` flag (`getUseCoworkPlugins`) has no CLI here;
`set_use_cowork_plugins` is its session state.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass

from app.tools.bash_tool.path_validation import expand_tilde
from app.utils.claudemd import is_env_truthy
from app.utils.tool_result_storage import config_home, format_file_size

logger = logging.getLogger("TDDOrchestrator.Plugins")

PLUGINS_DIR = "plugins"
COWORK_PLUGINS_DIR = "cowork_plugins"
PLUGIN_CACHE_DIR_ENV = "TDDAGENTS_PLUGIN_CACHE_DIR"
PLUGIN_SEED_DIR_ENV = "TDDAGENTS_PLUGIN_SEED_DIR"
USE_COWORK_PLUGINS_ENV = "TDDAGENTS_USE_COWORK_PLUGINS"

#: The plugin package folder, upstream's `.claude-plugin/`: it holds a plugin's `plugin.json`
#: and a marketplace's `marketplace.json`. **Recorded divergence (user decision, 2026-11-02)**:
#: TDDAgents renames it to `.tddagents-plugin/` everywhere (manifest and marketplace lookups,
#: validation, the loader), so a package written for upstream needs its folder renamed before
#: TDDAgents reads it. Every path to that folder is built from this one constant.
PLUGIN_PACKAGE_DIR = ".tddagents-plugin"

_use_cowork_plugins = False


def set_use_cowork_plugins(value: bool) -> None:
    """`setUseCoworkPlugins` (bootstrap state): the session's `--cowork` choice."""
    global _use_cowork_plugins
    _use_cowork_plugins = value


def get_use_cowork_plugins() -> bool:
    """`getUseCoworkPlugins`."""
    return _use_cowork_plugins


def get_plugins_directory_name() -> str:
    """`getPluginsDirectoryName`: session state, then the env var, then `plugins`."""
    if get_use_cowork_plugins():
        return COWORK_PLUGINS_DIR
    if is_env_truthy(os.environ.get(USE_COWORK_PLUGINS_ENV)):
        return COWORK_PLUGINS_DIR
    return PLUGINS_DIR


def get_plugins_directory() -> str:
    """`getPluginsDirectory`: the env override (tilde-expanded), else `<config home>/plugins`."""
    override = os.environ.get(PLUGIN_CACHE_DIR_ENV)
    if override:
        return expand_tilde(override)
    return os.path.join(str(config_home()), get_plugins_directory_name())


def get_plugin_seed_dirs() -> list[str]:
    """`getPluginSeedDirs`: the read-only seed directories, in precedence order (empty if unset)."""
    raw = os.environ.get(PLUGIN_SEED_DIR_ENV)
    if not raw:
        return []
    return [expand_tilde(part) for part in raw.split(os.pathsep) if part]


_UNSAFE_ID_CHARS = re.compile(r"[^a-zA-Z0-9\-_]")


def sanitize_plugin_id(plugin_id: str) -> str:
    """`sanitizePluginId`: the install-cache sanitizer's character class."""
    return _UNSAFE_ID_CHARS.sub("-", plugin_id)


def plugin_data_dir_path(plugin_id: str) -> str:
    """`pluginDataDirPath`: the data directory's path, not created."""
    return os.path.join(get_plugins_directory(), "data", sanitize_plugin_id(plugin_id))


def get_plugin_data_dir(plugin_id: str) -> str:
    """`getPluginDataDir`: `${CLAUDE_PLUGIN_DATA}`, created on call; it survives plugin updates."""
    directory = plugin_data_dir_path(plugin_id)
    os.makedirs(directory, exist_ok=True)
    return directory


@dataclass(frozen=True)
class DataDirSize:
    """`{bytes, human}`."""

    bytes: int
    human: str


async def get_plugin_data_dir_size(plugin_id: str) -> DataDirSize | None:
    """`getPluginDataDirSize`: `None` when the directory is absent or empty (no prompt needed)."""
    directory = plugin_data_dir_path(plugin_id)
    total = 0

    def walk(path: str) -> None:
        nonlocal total
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path)
                else:
                    try:
                        total += os.stat(entry.path).st_size
                    except OSError:
                        pass

    try:
        walk(directory)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None
    if total == 0:
        return None
    return DataDirSize(bytes=total, human=format_file_size(total))


async def delete_plugin_data_dir(plugin_id: str) -> None:
    """`deletePluginDataDir`: best-effort removal on the last-scope uninstall."""
    directory = plugin_data_dir_path(plugin_id)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to delete plugin data dir %s: %s", directory, exc)


__all__ = [
    "COWORK_PLUGINS_DIR",
    "PLUGINS_DIR",
    "PLUGIN_CACHE_DIR_ENV",
    "PLUGIN_SEED_DIR_ENV",
    "USE_COWORK_PLUGINS_ENV",
    "DataDirSize",
    "delete_plugin_data_dir",
    "get_plugin_data_dir",
    "get_plugin_data_dir_size",
    "get_plugin_seed_dirs",
    "get_plugins_directory",
    "get_plugins_directory_name",
    "get_use_cowork_plugins",
    "plugin_data_dir_path",
    "sanitize_plugin_id",
    "set_use_cowork_plugins",
]
