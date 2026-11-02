"""
The built-in plugin registry: plugins that ship with TDDAgents and that users enable or
disable (`<name>@builtin`).

Ported from `reference/claude-code/src/plugins/builtinPlugins.ts` -> `BUILTIN_MARKETPLACE_NAME`,
`registerBuiltinPlugin`, `isBuiltinPluginId`, `getBuiltinPluginDefinition`, `getBuiltinPlugins`,
`clearBuiltinPlugins`; `src/plugins/bundled/index.ts` -> `initBuiltinPlugins` (which, upstream as
here, registers nothing yet).

`get_builtin_plugins` reads the run's settings (`locations`) where upstream reads its
process-wide settings. Not ported (stage 3, the skills integration): `getBuiltinPluginSkillCommands`
and `skillDefinitionToCommand`, which turn a definition's `skills` into skill commands.
"""

from __future__ import annotations

from typing import cast

from app.types.plugin import BuiltinPluginDefinition, LoadedPlugin
from app.utils.plugins.schemas import PluginManifest
from app.utils.settings.settings import SettingsLocations, get_initial_settings

BUILTIN_MARKETPLACE_NAME = "builtin"

_BUILTIN_PLUGINS: dict[str, BuiltinPluginDefinition] = {}


def register_builtin_plugin(definition: BuiltinPluginDefinition) -> None:
    """`registerBuiltinPlugin`."""
    _BUILTIN_PLUGINS[definition.name] = definition


def is_builtin_plugin_id(plugin_id: str) -> bool:
    """`isBuiltinPluginId`: ends with `@builtin`."""
    return plugin_id.endswith(f"@{BUILTIN_MARKETPLACE_NAME}")


def get_builtin_plugin_definition(name: str) -> BuiltinPluginDefinition | None:
    """`getBuiltinPluginDefinition`."""
    return _BUILTIN_PLUGINS.get(name)


def get_builtin_plugins(locations: SettingsLocations) -> tuple[list[LoadedPlugin], list[LoadedPlugin]]:
    """
    `getBuiltinPlugins`: `(enabled, disabled)`; the user's `enabledPlugins` choice wins over the
    definition's `default_enabled` (itself `True` when unset). Unavailable plugins are omitted.
    """
    enabled_plugins = get_initial_settings(locations).get("enabledPlugins") or {}
    enabled: list[LoadedPlugin] = []
    disabled: list[LoadedPlugin] = []
    for name, definition in _BUILTIN_PLUGINS.items():
        if definition.is_available is not None and not definition.is_available():
            continue
        plugin_id = f"{name}@{BUILTIN_MARKETPLACE_NAME}"
        user_setting = enabled_plugins.get(plugin_id) if isinstance(enabled_plugins, dict) else None
        if user_setting is not None:
            is_enabled = user_setting is True
        else:
            is_enabled = definition.default_enabled if definition.default_enabled is not None else True
        manifest: dict[str, object] = {"name": name, "description": definition.description}
        if definition.version is not None:
            manifest["version"] = definition.version
        plugin = LoadedPlugin(
            name=name,
            manifest=cast(PluginManifest, manifest),
            path=BUILTIN_MARKETPLACE_NAME,
            source=plugin_id,
            repository=plugin_id,
            enabled=is_enabled,
            is_builtin=True,
            hooks_config=definition.hooks,
            mcp_servers=definition.mcp_servers,
        )
        (enabled if is_enabled else disabled).append(plugin)
    return enabled, disabled


def clear_builtin_plugins() -> None:
    """`clearBuiltinPlugins` (for tests)."""
    _BUILTIN_PLUGINS.clear()


def init_builtin_plugins() -> None:
    """`initBuiltinPlugins` (`plugins/bundled/index.ts`): no built-in plugin is registered yet, as upstream."""


__all__ = [
    "BUILTIN_MARKETPLACE_NAME",
    "clear_builtin_plugins",
    "get_builtin_plugin_definition",
    "get_builtin_plugins",
    "init_builtin_plugins",
    "is_builtin_plugin_id",
    "register_builtin_plugin",
]
