"""
The session's background housekeeping.

Ported from `reference/claude-code/src/utils/backgroundHousekeeping.ts` ->
`startBackgroundHousekeeping`: of its jobs, the plugin autoupdate
(`autoUpdateMarketplacesAndPluginsInBackground`, `app/utils/plugins/plugin_autoupdate.py`).
Upstream starts it once per process — a headless run when its MCP servers are connected
(`main.tsx`), the REPL at the first submitted prompt (`REPL.tsx`); `DevelopmentRun` calls it
there (`app/cli/print.py`).

Not ported (no subsystem here): magic docs, skill improvement, memory extraction, auto-dream,
the deep-link protocol, and the slow cleanups (`cleanupOldMessageFilesInBackground`,
`pruneShellSnapshots`, the npm cache and version cleanups).
"""

from __future__ import annotations

import asyncio
import logging

from app.utils.plugins.plugin_autoupdate import (
    auto_update_marketplaces_and_plugins_in_background,
)
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")


def start_background_housekeeping(locations: SettingsLocations) -> asyncio.Task[None]:
    """`startBackgroundHousekeeping`: the plugin autoupdate in the background (its task returned)."""
    return auto_update_marketplaces_and_plugins_in_background(locations)


__all__ = ["start_background_housekeeping"]
