"""
Background plugin autoupdate: refresh the marketplaces that auto-update, then update the
installed plugins from them (on disk only; a restart or `/reload-plugins` applies them).

Ported from `reference/claude-code/src/utils/plugins/pluginAutoupdate.ts` ->
`PluginAutoUpdateCallback`, `onPluginsAutoUpdated`, `getAutoUpdatedPluginNames`,
`getAutoUpdateEnabledMarketplaces`, `updatePlugin`, `updatePluginsForMarketplaces`,
`updatePlugins`, `autoUpdateMarketplacesAndPluginsInBackground`; and
`reference/claude-code/src/utils/config.ts` -> `shouldSkipPluginAutoupdate`,
`getAutoUpdaterDisabledReason` (its environment checks); `src/utils/privacyLevel.ts` ->
`getEssentialTrafficOnlyReason`.

Each installation is updated through `updatePluginOp` (`app/services/plugins/plugin_operations.py`),
bound to the run's settings. A caller may pass another `update_plugin_op(plugin_id, scope)`
(tests do), returning an object with upstream's `success`, `already_up_to_date`, `old_version`,
`new_version`, `message`.

Settings come from the run's `SettingsLocations`. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is
`TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC`; `DISABLE_AUTOUPDATER` and `FORCE_AUTOUPDATE_PLUGINS`
keep their names. Divergence: upstream's other auto-updater switches (`NODE_ENV=development`,
the global config's `autoUpdates`) have no counterpart, since TDDAgents has no self-updater.
`autoUpdateMarketplacesAndPluginsInBackground` returns the background task (upstream's `void`
promise) so a caller can keep or await it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.utils.claudemd import is_env_truthy
from app.utils.plugins.installed_plugins_manager import (
    get_pending_updates_details,
    has_pending_updates,
    is_installation_relevant_to_current_project,
    load_installed_plugins_from_disk,
)
from app.utils.plugins.marketplace_manager import (
    get_declared_marketplaces,
    load_known_marketplaces_config,
    refresh_marketplace,
)
from app.utils.plugins.plugin_identifier import parse_plugin_identifier
from app.utils.plugins.schemas import PluginInstallationEntry, is_marketplace_auto_update
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

DISABLE_AUTOUPDATER_ENV = "DISABLE_AUTOUPDATER"
FORCE_AUTOUPDATE_PLUGINS_ENV = "FORCE_AUTOUPDATE_PLUGINS"
DISABLE_NONESSENTIAL_TRAFFIC_ENV = "TDDAGENTS_DISABLE_NONESSENTIAL_TRAFFIC"

#: `PluginAutoUpdateCallback`.
PluginAutoUpdateCallback = Callable[[list[str]], None]


class PluginOperationResult(Protocol):
    """The fields of `updatePluginOp`'s result this module reads (read-only)."""

    @property
    def success(self) -> bool: ...

    @property
    def already_up_to_date(self) -> bool | None: ...

    @property
    def old_version(self) -> str | None: ...

    @property
    def new_version(self) -> str | None: ...

    @property
    def message(self) -> str: ...


#: `updatePluginOp(pluginId, scope)`.
UpdatePluginOp = Callable[[str, str], Awaitable[PluginOperationResult]]


def _default_update_plugin_op(locations: SettingsLocations) -> UpdatePluginOp:
    """The ported `updatePluginOp`, bound to the run's settings."""
    from app.services.plugins.plugin_operations import update_plugin_op

    async def update(plugin_id: str, scope: str) -> PluginOperationResult:
        return await update_plugin_op(plugin_id, scope, locations=locations)

    return update


_plugin_update_callback: PluginAutoUpdateCallback | None = None
_pending_notification: list[str] | None = None


def get_essential_traffic_only_reason() -> str | None:
    """`getEssentialTrafficOnlyReason`."""
    return DISABLE_NONESSENTIAL_TRAFFIC_ENV if os.environ.get(DISABLE_NONESSENTIAL_TRAFFIC_ENV) else None


def should_skip_plugin_autoupdate() -> bool:
    """`shouldSkipPluginAutoupdate`: the auto-updater is disabled and plugin updates are not forced."""
    disabled = is_env_truthy(os.environ.get(DISABLE_AUTOUPDATER_ENV)) or get_essential_traffic_only_reason() is not None
    return disabled and not is_env_truthy(os.environ.get(FORCE_AUTOUPDATE_PLUGINS_ENV))


def on_plugins_auto_updated(callback: PluginAutoUpdateCallback) -> Callable[[], None]:
    """
    `onPluginsAutoUpdated`: register the restart-notice callback; updates that finished before it
    registered are delivered at once. Returns the unregister function.
    """
    global _plugin_update_callback, _pending_notification
    _plugin_update_callback = callback
    if _pending_notification:
        callback(_pending_notification)
        _pending_notification = None

    def unregister() -> None:
        global _plugin_update_callback
        _plugin_update_callback = None

    return unregister


def get_auto_updated_plugin_names() -> list[str]:
    """`getAutoUpdatedPluginNames`: the names of the plugins with a pending update."""
    if not has_pending_updates():
        return []
    return [parse_plugin_identifier(d.plugin_id).name for d in get_pending_updates_details()]


async def _get_auto_update_enabled_marketplaces(locations: SettingsLocations) -> set[str]:
    """`getAutoUpdateEnabledMarketplaces`: lowercase names; a settings declaration wins over the JSON."""
    config = await load_known_marketplaces_config()
    declared = get_declared_marketplaces(locations)
    enabled: set[str] = set()
    for name, entry in config.items():
        declaration = declared.get(name)
        declared_auto_update = declaration.get("autoUpdate") if declaration is not None else None
        auto_update = declared_auto_update if declared_auto_update is not None else is_marketplace_auto_update(
            name, entry)
        if auto_update:
            enabled.add(name.lower())
    return enabled


async def _update_plugin(
    plugin_id: str, installations: list[PluginInstallationEntry], update_plugin_op: UpdatePluginOp
) -> str | None:
    """`updatePlugin`: the id when any installation was updated."""
    was_updated = False
    for installation in installations:
        try:
            result = await update_plugin_op(plugin_id, installation["scope"])
        except Exception as exc:  # noqa: BLE001 - one failed update must not stop the others
            logger.warning("Plugin autoupdate: error updating %s: %s", plugin_id, exc)
            continue
        if result.success and not result.already_up_to_date:
            was_updated = True
            logger.debug("Plugin autoupdate: updated %s from %s to %s", plugin_id, result.old_version,
                         result.new_version)
        elif not result.already_up_to_date:
            logger.warning("Plugin autoupdate: failed to update %s: %s", plugin_id, result.message)
    return plugin_id if was_updated else None


async def update_plugins_for_marketplaces(
    marketplace_names: set[str], locations: SettingsLocations, *, update_plugin_op: UpdatePluginOp | None = None
) -> list[str]:
    """
    `updatePluginsForMarketplaces`: update every installed plugin of these (lowercase)
    marketplaces, only the installations relevant to this project. Returns the updated ids.
    """
    update_op = update_plugin_op if update_plugin_op is not None else _default_update_plugin_op(locations)
    installed_plugins = load_installed_plugins_from_disk()
    plugin_ids = list(installed_plugins["plugins"])
    if not plugin_ids:
        return []

    async def one(plugin_id: str) -> str | None:
        marketplace = parse_plugin_identifier(plugin_id).marketplace
        if not marketplace or marketplace.lower() not in marketplace_names:
            return None
        installations = installed_plugins["plugins"].get(plugin_id) or []
        relevant = [i for i in installations if is_installation_relevant_to_current_project(i, locations)]
        if not relevant:
            return None
        return await _update_plugin(plugin_id, relevant, update_op)

    results = await asyncio.gather(*(one(p) for p in plugin_ids), return_exceptions=True)
    return [r for r in results if isinstance(r, str)]


async def _auto_update(locations: SettingsLocations, update_plugin_op: UpdatePluginOp | None) -> None:
    global _pending_notification
    if should_skip_plugin_autoupdate():
        logger.debug("Plugin autoupdate: skipped (auto-updater disabled)")
        return
    try:
        enabled = await _get_auto_update_enabled_marketplaces(locations)
        if not enabled:
            return

        async def refresh(name: str) -> None:
            try:
                await refresh_marketplace(name, None, disable_credential_helper=True)
            except Exception as exc:  # noqa: BLE001 - one marketplace's failure must not stop the others
                logger.warning("Plugin autoupdate: failed to refresh marketplace %s: %s", name, exc)

        refresh_results = await asyncio.gather(*(refresh(n) for n in sorted(enabled)), return_exceptions=True)
        failures = [r for r in refresh_results if isinstance(r, BaseException)]
        if failures:
            logger.warning("Plugin autoupdate: %d marketplace refresh(es) failed", len(failures))
        logger.debug("Plugin autoupdate: checking installed plugins")
        updated = await update_plugins_for_marketplaces(enabled, locations, update_plugin_op=update_plugin_op)
        if updated:
            if _plugin_update_callback is not None:
                _plugin_update_callback(updated)
            else:
                _pending_notification = updated
    except Exception as exc:  # noqa: BLE001 - a background update never fails the session
        logger.error("Plugin autoupdate failed: %s", exc)


def auto_update_marketplaces_and_plugins_in_background(
    locations: SettingsLocations, *, update_plugin_op: UpdatePluginOp | None = None
) -> asyncio.Task[None]:
    """
    `autoUpdateMarketplacesAndPluginsInBackground`: in a background task, refresh the
    auto-update marketplaces, update their installed plugins, and notify the registered callback
    (or keep the ids for it). Needs a running event loop.
    """
    return asyncio.ensure_future(_auto_update(locations, update_plugin_op))


def _reset_for_tests() -> None:
    global _plugin_update_callback, _pending_notification
    _plugin_update_callback = None
    _pending_notification = None


__all__ = [
    "DISABLE_AUTOUPDATER_ENV",
    "DISABLE_NONESSENTIAL_TRAFFIC_ENV",
    "FORCE_AUTOUPDATE_PLUGINS_ENV",
    "PluginAutoUpdateCallback",
    "PluginOperationResult",
    "UpdatePluginOp",
    "auto_update_marketplaces_and_plugins_in_background",
    "get_auto_updated_plugin_names",
    "get_essential_traffic_only_reason",
    "on_plugins_auto_updated",
    "should_skip_plugin_autoupdate",
    "update_plugins_for_marketplaces",
]
