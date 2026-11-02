"""
Which plugins are enabled, which scope owns each, which are missing, and installing the selected
ones.

Ported from `reference/claude-code/src/utils/plugins/pluginStartupCheck.ts` -> `checkEnabledPlugins`,
`getPluginEditableScopes`, `isPersistableScope`, `settingSourceToScope`, `getInstalledPlugins`,
`findMissingPlugins`, `PluginInstallResult`, `installSelectedPlugins`.

Settings come from the run's `SettingsLocations`; upstream's `getCwd()` (the project path of a
project/local installation) is `locations.project_root`. `installSelectedPlugins` writes only the
`enabledPlugins` key (`update_settings_for_source` merges it into the file), the same result as
upstream's rewrite of the whole document with that key replaced.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.types.permissions import PermissionRuleSource
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.add_dir_plugin_settings import get_add_dir_enabled_plugins
from app.utils.plugins.installed_plugins_manager import (
    get_in_memory_installed_plugins,
    migrate_from_enabled_plugins,
)
from app.utils.plugins.marketplace_manager import get_plugin_by_id
from app.utils.plugins.plugin_identifier import (
    SETTING_SOURCE_TO_SCOPE,
    ExtendedPluginScope,
    InstallableScope,
    scope_to_setting_source,
)
from app.utils.plugins.plugin_installation_helpers import (
    PluginInstallationInfo,
    cache_and_register_plugin,
    register_plugin_installation,
)
from app.utils.plugins.schemas import is_local_plugin_source
from app.utils.settings.settings import SettingsLocations, get_initial_settings, get_settings_for_source

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: The fire-and-forget `migrateFromEnabledPlugins` tasks, kept referenced.
_BACKGROUND: set[asyncio.Task[None]] = set()


async def check_enabled_plugins(locations: SettingsLocations) -> list[str]:
    """
    `checkEnabledPlugins`: the enabled `plugin@marketplace` ids, `--add-dir` ones first, then the
    merged settings (policy highest), whose `false` removes an `--add-dir` enable.
    """
    settings = get_initial_settings(locations)
    enabled: list[str] = []
    for plugin_id, value in get_add_dir_enabled_plugins(locations).items():
        if "@" in plugin_id and value:
            enabled.append(plugin_id)
    for plugin_id, value in (settings.get("enabledPlugins") or {}).items():
        if "@" not in plugin_id:
            continue
        if value:
            if plugin_id not in enabled:
                enabled.append(plugin_id)
        elif plugin_id in enabled:
            enabled.remove(plugin_id)
    return enabled


_SCOPE_SOURCES: tuple[tuple[ExtendedPluginScope, PermissionRuleSource], ...] = (
    ("managed", PermissionRuleSource.POLICY_SETTINGS),
    ("user", PermissionRuleSource.USER_SETTINGS),
    ("project", PermissionRuleSource.PROJECT_SETTINGS),
    ("local", PermissionRuleSource.LOCAL_SETTINGS),
    ("flag", PermissionRuleSource.FLAG_SETTINGS),
)


def get_plugin_editable_scopes(locations: SettingsLocations) -> dict[str, ExtendedPluginScope]:
    """
    `getPluginEditableScopes`: the user-editable scope owning each enabled plugin, lowest
    precedence first (`--add-dir` as `flag`, managed, user, project, local, flag); `false`
    removes. Not the authoritative "is it enabled" check (that is `check_enabled_plugins`).
    """
    result: dict[str, ExtendedPluginScope] = {}
    add_dir_plugins = get_add_dir_enabled_plugins(locations)
    for plugin_id, value in add_dir_plugins.items():
        if "@" not in plugin_id:
            continue
        if value is True:
            result[plugin_id] = "flag"
        elif value is False:
            result.pop(plugin_id, None)
    for scope, source in _SCOPE_SOURCES:
        settings = get_settings_for_source(source, locations)
        enabled_plugins = (settings or {}).get("enabledPlugins")
        if not enabled_plugins:
            continue
        for plugin_id, value in enabled_plugins.items():
            if "@" not in plugin_id:
                continue
            if plugin_id in add_dir_plugins and add_dir_plugins[plugin_id] != value:
                logger.debug("Plugin %s from --add-dir (%s) overridden by %s (%s)", plugin_id,
                             add_dir_plugins[plugin_id], source, value)
            if value is True:
                result[plugin_id] = scope
            elif value is False:
                result.pop(plugin_id, None)
    logger.debug("Found %d enabled plugins with scopes: %s", len(result),
                 ", ".join(f"{i}({s})" for i, s in result.items()))
    return result


def is_persistable_scope(scope: ExtendedPluginScope) -> bool:
    """`isPersistableScope`: every scope but the session-only `flag`."""
    return scope != "flag"


def setting_source_to_scope(source: PermissionRuleSource) -> ExtendedPluginScope:
    """`settingSourceToScope` (`SETTING_SOURCE_TO_SCOPE`)."""
    return SETTING_SOURCE_TO_SCOPE[source]


async def get_installed_plugins(locations: SettingsLocations) -> list[str]:
    """
    `getInstalledPlugins`: the installed plugin ids, from the session snapshot; the sync from
    `enabledPlugins` (`migrateFromEnabledPlugins`) starts in the background, as upstream.
    """
    async def sync() -> None:
        try:
            await migrate_from_enabled_plugins(locations)
        except Exception as exc:  # noqa: BLE001 - upstream's `.catch(logError)`
            logger.error("Failed to sync installed plugins: %s", exc)

    task = asyncio.ensure_future(sync())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    installed = list(get_in_memory_installed_plugins()["plugins"])
    logger.debug("Found %d installed plugins", len(installed))
    return installed


async def find_missing_plugins(enabled_plugins: list[str], locations: SettingsLocations) -> list[str]:
    """`findMissingPlugins`: the enabled, not installed ids that some marketplace lists, in order."""
    try:
        installed = await get_installed_plugins(locations)
        not_installed = [p for p in enabled_plugins if p not in installed]

        async def lookup(plugin_id: str) -> bool:
            try:
                return await get_plugin_by_id(plugin_id) is not None
            except Exception as exc:  # noqa: BLE001 - an unknown plugin is simply not found
                logger.debug("Failed to check plugin %s in marketplace: %s", plugin_id, exc)
                return False

        found = await asyncio.gather(*(lookup(p) for p in not_installed))
        return [p for p, ok in zip(not_installed, found, strict=True) if ok]
    except Exception as exc:  # noqa: BLE001 - upstream logs and reports nothing missing
        logger.error("%s", exc)
        return []


@dataclass
class PluginInstallFailure:
    """One `PluginInstallResult.failed` entry."""

    name: str
    error: str


@dataclass
class PluginInstallResult:
    """`PluginInstallResult`."""

    installed: list[str] = field(default_factory=list)
    failed: list[PluginInstallFailure] = field(default_factory=list)


async def install_selected_plugins(
    plugins_to_install: list[str],
    locations: SettingsLocations,
    on_progress: Callable[[str, int, int], None] | None = None,
    scope: InstallableScope = "user",
) -> PluginInstallResult:
    """
    `installSelectedPlugins`: cache and register each external plugin (register a local one in
    place), enable them all in the scope's settings file, and report what failed.
    """
    project_path = str(locations.project_root) if scope != "user" else None
    setting_source = scope_to_setting_source(scope)
    settings = get_settings_for_source(setting_source, locations) or {}
    updated_enabled_plugins: dict[str, Any] = dict(settings.get("enabledPlugins") or {})
    result = PluginInstallResult()
    total = len(plugins_to_install)
    for index, plugin_id in enumerate(plugins_to_install):
        if not plugin_id:
            continue
        if on_progress is not None:
            on_progress(plugin_id, index + 1, total)
        try:
            plugin_info = await get_plugin_by_id(plugin_id)
            if plugin_info is None:
                result.failed.append(PluginInstallFailure(name=plugin_id, error="Plugin not found in any marketplace"))
                continue
            entry = plugin_info.entry
            if not is_local_plugin_source(entry["source"]):
                await cache_and_register_plugin(plugin_id, entry, scope, project_path)
            else:
                register_plugin_installation(
                    PluginInstallationInfo(
                        plugin_id=plugin_id,
                        install_path=os.path.normpath(
                            os.path.join(plugin_info.marketplace_install_location, str(entry["source"]))
                        ),
                        version=entry.get("version"),
                    ),
                    scope,
                    project_path,
                )
            updated_enabled_plugins[plugin_id] = True
            result.installed.append(plugin_id)
        except Exception as exc:  # noqa: BLE001 - one failed install must not stop the others
            result.failed.append(PluginInstallFailure(name=plugin_id, error=str(exc)))
            logger.error("%s", exc)
    update_settings_for_source(setting_source, locations, {"enabledPlugins": updated_enabled_plugins})
    return result


__all__ = [
    "PluginInstallFailure",
    "PluginInstallResult",
    "check_enabled_plugins",
    "find_missing_plugins",
    "get_installed_plugins",
    "get_plugin_editable_scopes",
    "install_selected_plugins",
    "is_persistable_scope",
    "setting_source_to_scope",
]
