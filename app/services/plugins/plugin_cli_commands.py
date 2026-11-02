"""
The plugin CLI commands: the plugin operations with their console output and exit code.

Ported from `reference/claude-code/src/services/plugins/pluginCliCommands.ts` ->
`handlePluginCommandError`, `installPlugin`, `uninstallPlugin`, `enablePlugin`, `disablePlugin`,
`disableAllPlugins`, `updatePluginCli`.

Each returns the process exit code (upstream calls `process.exit` / `gracefulShutdown`): 0 on
success, 1 with `✘ Failed to <operation>: <message>` on stderr otherwise. Output goes through
`out` / `err` (stdout / stderr by default). Divergence: the `tengu_plugin_*` analytics events
are not ported.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from app.services.plugins.plugin_operations import (
    VALID_INSTALLABLE_SCOPES,
    VALID_UPDATE_SCOPES,
    PluginOperationResult,
    PluginUpdateResult,
    disable_all_plugins_op,
    disable_plugin_op,
    enable_plugin_op,
    install_plugin_op,
    uninstall_plugin_op,
    update_plugin_op,
)
from app.utils.plugins.plugin_identifier import InstallableScope
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `figures.tick` / `figures.cross` (Unicode terminals).
TICK = "✔"
CROSS = "✘"

Writer = Callable[[str], None]


def _stdout(text: str) -> None:
    print(text)


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def handle_plugin_command_error(error: BaseException, command: str, plugin: str | None, err: Writer) -> int:
    """`handlePluginCommandError`: the failure on stderr, exit code 1."""
    logger.debug("plugin %s failed: %s", command, error)
    if plugin:
        operation = f'{command} plugin "{plugin}"'
    elif command == "disable-all":
        operation = "disable all plugins"
    else:
        operation = f"{command} plugins"
    err(f"{CROSS} Failed to {operation}: {error}")
    return 1


def _checked(result: PluginOperationResult | PluginUpdateResult) -> None:
    if not result.success:
        raise RuntimeError(result.message)


async def install_plugin(
    plugin: str, scope: InstallableScope = "user", *, locations: SettingsLocations, out: Writer = _stdout,
    err: Writer = _stderr,
) -> int:
    """`installPlugin`."""
    try:
        out(f'Installing plugin "{plugin}"...')
        result = await install_plugin_op(plugin, scope, locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "install", plugin, err)


async def uninstall_plugin(
    plugin: str, scope: InstallableScope = "user", keep_data: bool = False, *, locations: SettingsLocations,
    out: Writer = _stdout, err: Writer = _stderr,
) -> int:
    """`uninstallPlugin`."""
    try:
        result = await uninstall_plugin_op(plugin, scope, not keep_data, locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "uninstall", plugin, err)


async def enable_plugin(
    plugin: str, scope: InstallableScope | None = None, *, locations: SettingsLocations, out: Writer = _stdout,
    err: Writer = _stderr,
) -> int:
    """`enablePlugin`."""
    try:
        result = await enable_plugin_op(plugin, scope, locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "enable", plugin, err)


async def disable_plugin(
    plugin: str, scope: InstallableScope | None = None, *, locations: SettingsLocations, out: Writer = _stdout,
    err: Writer = _stderr,
) -> int:
    """`disablePlugin`."""
    try:
        result = await disable_plugin_op(plugin, scope, locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "disable", plugin, err)


async def disable_all_plugins(*, locations: SettingsLocations, out: Writer = _stdout, err: Writer = _stderr) -> int:
    """`disableAllPlugins`."""
    try:
        result = await disable_all_plugins_op(locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "disable-all", None, err)


async def update_plugin_cli(
    plugin: str, scope: str, *, locations: SettingsLocations, out: Writer = _stdout, err: Writer = _stderr
) -> int:
    """`updatePluginCli`."""
    try:
        out(f'Checking for updates for plugin "{plugin}" at {scope} scope…')
        result = await update_plugin_op(plugin, scope, locations=locations)
        _checked(result)
        out(f"{TICK} {result.message}")
        return 0
    except Exception as error:  # noqa: BLE001 - upstream reports any failure and exits 1
        return handle_plugin_command_error(error, "update", plugin, err)


__all__ = [
    "CROSS",
    "TICK",
    "VALID_INSTALLABLE_SCOPES",
    "VALID_UPDATE_SCOPES",
    "disable_all_plugins",
    "disable_plugin",
    "enable_plugin",
    "handle_plugin_command_error",
    "install_plugin",
    "uninstall_plugin",
    "update_plugin_cli",
]
