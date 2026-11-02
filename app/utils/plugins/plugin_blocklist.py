"""
Plugin delisting: find installed plugins their marketplace no longer lists, auto-uninstall them
and flag them.

Ported from `reference/claude-code/src/utils/plugins/pluginBlocklist.ts` -> `detectDelistedPlugins`,
`detectAndUninstallDelistedPlugins`.

Seam: upstream calls `uninstallPluginOp` (`src/services/plugins/pluginOperations.ts`), which stage 3
ports. Until then `detect_and_uninstall_delisted_plugins` takes it as the required keyword
argument `uninstall_plugin_op(plugin_id, scope)`; stage 3 passes its port (or makes it the
default).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.utils.plugins.installed_plugins_manager import load_installed_plugins_v2
from app.utils.plugins.marketplace_manager import (
    get_marketplace,
    load_known_marketplaces_config_safe,
)
from app.utils.plugins.plugin_flagging import (
    add_flagged_plugin,
    get_flagged_plugins,
    load_flagged_plugins,
)
from app.utils.plugins.schemas import InstalledPluginsFileV2, PluginMarketplace

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `uninstallPluginOp(pluginId, scope)` (stage 3).
UninstallPluginOp = Callable[[str, str], Awaitable[object]]

_USER_SCOPES = ("user", "project", "local")


def detect_delisted_plugins(
    installed_plugins: InstalledPluginsFileV2, marketplace: PluginMarketplace, marketplace_name: str
) -> list[str]:
    """`detectDelistedPlugins`: installed `*@<marketplace_name>` ids the marketplace no longer lists."""
    listed = {p["name"] for p in marketplace["plugins"]}
    suffix = f"@{marketplace_name}"
    delisted: list[str] = []
    for plugin_id in installed_plugins["plugins"]:
        if not plugin_id.endswith(suffix):
            continue
        if plugin_id[: -len(suffix)] not in listed:
            delisted.append(plugin_id)
    return delisted


async def detect_and_uninstall_delisted_plugins(*, uninstall_plugin_op: UninstallPluginOp) -> list[str]:
    """
    `detectAndUninstallDelistedPlugins`: for marketplaces with `forceRemoveDeletedPlugins`,
    uninstall each newly delisted plugin from its user-controllable scopes and flag it. Plugins
    installed only at managed scope are left to the admin. Returns the newly flagged ids.
    """
    await load_flagged_plugins()
    installed = load_installed_plugins_v2()
    already_flagged = get_flagged_plugins()
    known = await load_known_marketplaces_config_safe()
    newly_flagged: list[str] = []
    for marketplace_name in known:
        try:
            marketplace = await get_marketplace(marketplace_name)
            if not marketplace.get("forceRemoveDeletedPlugins"):
                continue
            for plugin_id in detect_delisted_plugins(installed, marketplace, marketplace_name):
                if plugin_id in already_flagged:
                    continue
                installations = installed["plugins"].get(plugin_id) or []
                if not any(i["scope"] in _USER_SCOPES for i in installations):
                    continue
                for installation in installations:
                    scope = installation["scope"]
                    if scope not in _USER_SCOPES:
                        continue
                    try:
                        await uninstall_plugin_op(plugin_id, scope)
                    except Exception as exc:  # noqa: BLE001 - one failed scope must not stop the rest
                        logger.error("Failed to auto-uninstall delisted plugin %s from %s: %s", plugin_id, scope, exc)
                await add_flagged_plugin(plugin_id)
                newly_flagged.append(plugin_id)
        except Exception as exc:  # noqa: BLE001 - the marketplace may not be available yet
            logger.warning('Failed to check for delisted plugins in "%s": %s', marketplace_name, exc)
    return newly_flagged


__all__ = ["UninstallPluginOp", "detect_and_uninstall_delisted_plugins", "detect_delisted_plugins"]
