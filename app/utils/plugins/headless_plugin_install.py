"""
Plugin installation for headless (`-p`) runs: reconcile the declared marketplaces and enforce
delisting, without `AppState` updates.

Ported from `reference/claude-code/src/utils/plugins/headlessPluginInstall.ts` ->
`installPluginsForHeadless`.

With the zip cache on (`TDDAGENTS_PLUGIN_USE_ZIP_CACHE`), the cache's directories are created,
marketplaces whose source the zip cache cannot hold are skipped, their JSONs are synced into the
cache, and the session's extraction directory is removed at process exit (upstream's
`registerCleanup`, here `atexit`).

Delisting uninstalls through `uninstallPluginOp` (`app/services/plugins/plugin_operations.py`),
bound to the run's settings unless the caller passes another `uninstall_plugin_op` (see
`plugin_blocklist.detect_and_uninstall_delisted_plugins`). Settings come from the run's
`SettingsLocations`. Divergence: the `tengu_headless_plugin_install` analytics event and
the diagnostics timing are not ported.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
from typing import Any, cast

from app.utils.plugins.marketplace_manager import (
    clear_marketplaces_cache,
    get_declared_marketplaces,
    register_seed_marketplaces,
)
from app.utils.plugins.plugin_blocklist import UninstallPluginOp, detect_and_uninstall_delisted_plugins
from app.utils.plugins.plugin_identifier import InstallableScope
from app.utils.plugins.plugin_loader import clear_plugin_cache
from app.utils.plugins.reconciler import ReconcileOptions, ReconcileProgressEvent, reconcile_marketplaces
from app.utils.plugins.zip_cache import (
    cleanup_session_plugin_cache,
    get_zip_cache_marketplaces_dir,
    get_zip_cache_plugins_dir,
    is_marketplace_source_supported_by_zip_cache,
    is_plugin_zip_cache_enabled,
)
from app.utils.plugins.zip_cache_adapters import sync_marketplaces_to_zip_cache
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

_cleanup_registered = False


def _register_session_cache_cleanup() -> None:
    """`registerCleanup(cleanupSessionPluginCache)`, once per process."""
    global _cleanup_registered
    if _cleanup_registered:
        return
    _cleanup_registered = True
    atexit.register(lambda: asyncio.run(cleanup_session_plugin_cache()))


def _on_progress(event: ReconcileProgressEvent) -> None:
    if event.type == "installed":
        logger.debug("installPluginsForHeadless: installed marketplace %s", event.name)
    elif event.type == "failed":
        logger.debug("installPluginsForHeadless: failed to install marketplace %s: %s", event.name, event.error)


def _skip_unsupported(_name: str, source: dict[str, Any]) -> bool:
    return not is_marketplace_source_supported_by_zip_cache(source)


def _default_uninstall_plugin_op(locations: SettingsLocations) -> UninstallPluginOp:
    """The ported `uninstallPluginOp`, bound to the run's settings."""
    from app.services.plugins.plugin_operations import uninstall_plugin_op

    async def uninstall(plugin_id: str, scope: str) -> object:
        return await uninstall_plugin_op(plugin_id, cast(InstallableScope, scope), locations=locations)

    return uninstall


async def install_plugins_for_headless(
    locations: SettingsLocations, *, uninstall_plugin_op: UninstallPluginOp | None = None
) -> bool:
    """
    `installPluginsForHeadless`: register the seed marketplaces, reconcile the declared ones,
    sync the zip cache, uninstall newly delisted plugins. True when anything changed (the
    caller then refreshes the plugin state); any failure is logged and reads as False.
    """
    if uninstall_plugin_op is None:
        uninstall_plugin_op = _default_uninstall_plugin_op(locations)
    zip_cache_mode = is_plugin_zip_cache_enabled()
    logger.debug("installPluginsForHeadless: starting%s", " (zip cache mode)" if zip_cache_mode else "")
    seed_changed = await register_seed_marketplaces()
    if seed_changed:
        clear_marketplaces_cache()
        clear_plugin_cache("headlessPluginInstall: seed marketplaces registered")
    if zip_cache_mode:
        os.makedirs(get_zip_cache_marketplaces_dir(), exist_ok=True)
        os.makedirs(get_zip_cache_plugins_dir(), exist_ok=True)
    declared_count = len(get_declared_marketplaces(locations))
    plugins_changed = seed_changed
    try:
        if declared_count == 0:
            logger.debug("installPluginsForHeadless: no marketplaces declared")
        else:
            options = ReconcileOptions(skip=_skip_unsupported if zip_cache_mode else None, on_progress=_on_progress)
            reconcile_result = await reconcile_marketplaces(locations, options)
            if reconcile_result.skipped:
                logger.debug(
                    "installPluginsForHeadless: skipped %d marketplace(s) unsupported by zip cache: %s",
                    len(reconcile_result.skipped), ", ".join(reconcile_result.skipped),
                )
            if len(reconcile_result.installed) + len(reconcile_result.updated) > 0:
                clear_marketplaces_cache()
                clear_plugin_cache("headlessPluginInstall: marketplaces reconciled")
                plugins_changed = True
        if zip_cache_mode:
            await sync_marketplaces_to_zip_cache()
        newly_delisted = await detect_and_uninstall_delisted_plugins(uninstall_plugin_op=uninstall_plugin_op)
        if newly_delisted:
            plugins_changed = True
        if plugins_changed:
            clear_plugin_cache("headlessPluginInstall: plugins changed")
        if zip_cache_mode:
            _register_session_cache_cleanup()
        return plugins_changed
    except Exception as exc:  # noqa: BLE001 - a failed headless install never fails the run
        logger.error("installPluginsForHeadless failed: %s", exc)
        return False


__all__ = ["install_plugins_for_headless"]
