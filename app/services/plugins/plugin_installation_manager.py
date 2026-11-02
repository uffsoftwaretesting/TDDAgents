"""
Background marketplace installation: reconcile the declared marketplaces without blocking
startup, report progress in `AppState.plugins.installation_status`, then refresh the plugins.

Ported from `reference/claude-code/src/services/plugins/PluginInstallationManager.ts` ->
`updateMarketplaceStatus`, `performBackgroundPluginInstallations`.

A newly installed marketplace auto-refreshes the plugins (`refresh_active_plugins`; on failure
the run falls back to `needs_refresh`); an updated one only sets `needs_refresh`, so the user
chooses when to run `/reload-plugins`. Settings come from the run's `SettingsLocations`.
Divergence: the `tengu_marketplace_background_install` analytics event is not ported.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from app.state.app_state_store import (
    AppState,
    InstallationState,
    InstallationStatus,
    MarketplaceInstallationStatus,
)
from app.utils.plugins.marketplace_manager import (
    clear_marketplaces_cache,
    get_declared_marketplaces,
    load_known_marketplaces_config,
)
from app.utils.plugins.plugin_loader import clear_plugin_cache
from app.utils.plugins.reconciler import (
    ReconcileOptions,
    ReconcileProgressEvent,
    diff_marketplaces,
    reconcile_marketplaces,
)
from app.utils.plugins.refresh import SetAppState, refresh_active_plugins
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")


def _update_marketplace_status(
    set_app_state: SetAppState, name: str, status: InstallationState, error: str | None = None
) -> None:
    """`updateMarketplaceStatus`."""

    def update(prev: AppState) -> AppState:
        current = prev.plugins.installation_status
        marketplaces = tuple(
            replace(m, status=status, error=error) if m.name == name else m for m in current.marketplaces
        )
        return replace(prev, plugins=replace(prev.plugins, installation_status=replace(
            current, marketplaces=marketplaces)))

    set_app_state(update)


def set_needs_refresh(set_app_state: SetAppState) -> None:
    """Upstream's inline `needsRefresh: true` update (a no-op when it is already set)."""

    def update(prev: AppState) -> AppState:
        if prev.plugins.needs_refresh:
            return prev
        return replace(prev, plugins=replace(prev.plugins, needs_refresh=True))

    set_app_state(update)


async def perform_background_plugin_installations(
    set_app_state: SetAppState, locations: SettingsLocations,
    refresh: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """
    `performBackgroundPluginInstallations`: mark the missing and changed marketplaces pending,
    reconcile them (progress into `installation_status`), then refresh after a new install or
    flag `needs_refresh` after updates. Any failure is logged, never raised.

    `refresh` is the session's own `refreshActivePlugins` (the run's `refresh_plugins`: hooks,
    commands, agents and MCP servers reloaded, as upstream's REPL picks up the AppState change);
    without it the plugins are refreshed alone.
    """
    logger.debug("performBackgroundPluginInstallations called")
    try:
        declared = get_declared_marketplaces(locations)
        try:
            materialized: Mapping[str, Any] = await load_known_marketplaces_config()
        except Exception:  # noqa: BLE001 - upstream's `.catch(() => ({}))`
            materialized = {}
        diff = diff_marketplaces(declared, materialized, project_root=str(locations.project_root))
        pending_names = [*diff.missing, *(c.name for c in diff.source_changed)]

        def initialize(prev: AppState) -> AppState:
            status = InstallationStatus(
                marketplaces=tuple(MarketplaceInstallationStatus(name=n, status="pending") for n in pending_names),
                plugins=(),
            )
            return replace(prev, plugins=replace(prev.plugins, installation_status=status))

        set_app_state(initialize)
        if not pending_names:
            return
        logger.debug("Installing %d marketplace(s) in background", len(pending_names))

        def on_progress(event: ReconcileProgressEvent) -> None:
            if event.type == "installing":
                _update_marketplace_status(set_app_state, event.name, "installing")
            elif event.type == "installed":
                _update_marketplace_status(set_app_state, event.name, "installed")
            elif event.type == "failed":
                _update_marketplace_status(set_app_state, event.name, "failed", event.error)

        result = await reconcile_marketplaces(locations, ReconcileOptions(on_progress=on_progress))
        if result.installed:
            clear_marketplaces_cache()
            logger.debug("Auto-refreshing plugins after %d new marketplace(s) installed", len(result.installed))
            try:
                if refresh is not None:
                    await refresh()
                else:
                    await refresh_active_plugins(set_app_state, locations)
            except Exception as exc:  # noqa: BLE001 - falls back to the needs-refresh notice, as upstream
                logger.warning("Auto-refresh failed, falling back to needsRefresh: %s", exc)
                clear_plugin_cache("performBackgroundPluginInstallations: auto-refresh failed")
                set_needs_refresh(set_app_state)
        elif result.updated:
            clear_marketplaces_cache()
            clear_plugin_cache("performBackgroundPluginInstallations: marketplaces reconciled")
            set_needs_refresh(set_app_state)
    except Exception as exc:  # noqa: BLE001 - a background installation never fails the session
        logger.error("Background plugin installation failed: %s", exc)


__all__ = ["perform_background_plugin_installations", "set_needs_refresh"]
