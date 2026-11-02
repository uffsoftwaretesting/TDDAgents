"""
The interactive startup's plugin checks: register the seed marketplaces, then start the
background marketplace installation.

Ported from `reference/claude-code/src/utils/plugins/performStartupChecks.tsx` -> `performStartupChecks`
(its non-UI part: the module has no rendering).

Upstream runs only after the "trust this folder" dialog was accepted (`checkHasTrustDialogAccepted`),
so a repository's settings cannot install marketplaces without consent. TDDAgents has no trust
dialog: the caller passes `trust_accepted`, and `False` skips the installations as upstream does.
Settings come from the run's `SettingsLocations`.
"""

from __future__ import annotations

import logging

from app.services.plugins.plugin_installation_manager import (
    perform_background_plugin_installations,
    set_needs_refresh,
)
from app.utils.plugins.marketplace_manager import clear_marketplaces_cache, register_seed_marketplaces
from app.utils.plugins.plugin_loader import clear_plugin_cache
from app.utils.plugins.refresh import SetAppState
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")


async def perform_startup_checks(
    set_app_state: SetAppState, locations: SettingsLocations, *, trust_accepted: bool
) -> None:
    """
    `performStartupChecks`: without trust, nothing; otherwise register the seed marketplaces
    (a change clears the marketplace and plugin caches and sets `needs_refresh`), then run the
    background installations. A failure is logged, never raised.
    """
    logger.debug("performStartupChecks called")
    if not trust_accepted:
        logger.debug("Trust not accepted for current directory - skipping plugin installations")
        return
    try:
        logger.debug("Starting background plugin installations")
        if await register_seed_marketplaces():
            clear_marketplaces_cache()
            clear_plugin_cache("performStartupChecks: seed marketplaces changed")
            set_needs_refresh(set_app_state)
        await perform_background_plugin_installations(set_app_state, locations)
    except Exception as exc:  # noqa: BLE001 - startup checks never block the session
        logger.debug("Error initiating background plugin installations: %s", exc)


__all__ = ["perform_startup_checks"]
