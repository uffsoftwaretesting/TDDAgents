"""
Plugin option storage and the `${...}` substitutions plugins use.

Ported from `reference/claude-code/src/utils/plugins/pluginOptionsStorage.ts` -> `PluginOptionValues`,
`PluginOptionSchema`, `getPluginStorageId`, `loadPluginOptions` (memoized), `clearPluginOptionsCache`,
`savePluginOptions`, `deletePluginOptions`, `getUnconfiguredOptions`, `substitutePluginVariables`,
`substituteUserConfigVariables`, `substituteUserConfigInContent`.

Options a plugin declares in `manifest.userConfig` are stored split by `sensitive`: sensitive
values in secure storage (`pluginSecrets[pluginId]`), the rest in user settings
(`pluginConfigs[pluginId].options`). Loading merges both, secure storage winning.

`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and `${user_config.KEY}` are part of the plugin
format plugins are written against, so they are kept verbatim (no product substitution).

`locations` names the run's settings sources (upstream reads its process-wide settings), so the
load memo is keyed by plugin id and `locations` (upstream's, by plugin id, serves its one settings
set): `clear_plugin_options_cache` empties it (a save or delete does so itself). A stored `null`
is a value, not an absent key, as upstream's `undefined` checks.

Divergence: upstream's `savePluginOptions` writes the whole *merged* settings object into user
settings (its own TODO notes this can leak project-scope keys into `~/.claude/settings.json`);
here only the `pluginConfigs[pluginId].options` patch is written, which leaves the same
`pluginConfigs` result without copying other sources' keys into the user file.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any, Protocol, TypeAlias

from app.types.permissions import PermissionRuleSource
from app.types.plugin import LoadedPlugin
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.plugins.mcpb_handler import (
    UserConfigSchema,
    UserConfigValues,
    validate_user_config,
)
from app.utils.plugins.plugin_directories import get_plugin_data_dir
from app.utils.secure_storage.index import get_secure_storage
from app.utils.settings.settings import SettingsLocations, get_initial_settings

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `PluginOptionValues`.
PluginOptionValues: TypeAlias = UserConfigValues
#: `PluginOptionSchema`.
PluginOptionSchema: TypeAlias = UserConfigSchema

_load_cache: dict[tuple[str, SettingsLocations], PluginOptionValues] = {}

_PLUGIN_ROOT = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}")
_PLUGIN_DATA = re.compile(r"\$\{CLAUDE_PLUGIN_DATA\}")
_USER_CONFIG = re.compile(r"\$\{user_config\.([^}]+)\}")


class PluginPathLike(Protocol):
    """`{ path: string; source?: string }`: a `LoadedPlugin`, or a skill root without a plugin."""

    @property
    def path(self) -> str: ...

    @property
    def source(self) -> str | None: ...


def get_plugin_storage_id(plugin: LoadedPlugin) -> str:
    """`getPluginStorageId`: the key in `pluginConfigs` and `pluginSecrets` (`name@marketplace`)."""
    return plugin.source


def _plugin_configs(settings: Mapping[str, Any]) -> dict[str, Any]:
    configs = settings.get("pluginConfigs")
    return configs if isinstance(configs, dict) else {}


def _settings_options(settings: Mapping[str, Any], plugin_id: str) -> dict[str, Any]:
    entry = _plugin_configs(settings).get(plugin_id)
    options = entry.get("options") if isinstance(entry, dict) else None
    return dict(options) if isinstance(options, dict) else {}


def _stored_secrets(data: Mapping[str, Any] | None, plugin_id: str) -> dict[str, Any] | None:
    secrets = (data or {}).get("pluginSecrets")
    entry = secrets.get(plugin_id) if isinstance(secrets, dict) else None
    return dict(entry) if isinstance(entry, dict) else None


def load_plugin_options(plugin_id: str, locations: SettingsLocations) -> PluginOptionValues:
    """`loadPluginOptions`: settings options merged with secure-storage secrets (secrets win); memoized."""
    cached = _load_cache.get((plugin_id, locations))
    if cached is not None:
        return cached
    non_sensitive = _settings_options(get_initial_settings(locations), plugin_id)
    sensitive = _stored_secrets(get_secure_storage().read(), plugin_id) or {}
    merged: PluginOptionValues = {**non_sensitive, **sensitive}
    _load_cache[(plugin_id, locations)] = merged
    return merged


def clear_plugin_options_cache() -> None:
    """`clearPluginOptionsCache`."""
    _load_cache.clear()


def _js_string(value: Any) -> str:
    """JavaScript's `String(value)` for the option value types."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join(_js_string(v) for v in value)
    return str(value)


def save_plugin_options(
    plugin_id: str,
    values: Mapping[str, Any],
    schema: Mapping[str, Mapping[str, Any]],
    locations: SettingsLocations,
) -> None:
    """
    `savePluginOptions`: sensitive values to secure storage first (a failure raises before
    settings are touched), then the rest to user settings. Keys saved to one store are scrubbed
    from the other.
    """
    non_sensitive: dict[str, Any] = {}
    sensitive: dict[str, str] = {}
    for key, value in values.items():
        if (schema.get(key) or {}).get("sensitive") is True:
            sensitive[key] = _js_string(value)
        else:
            non_sensitive[key] = value

    storage = get_secure_storage()
    existing_secret = _stored_secrets(storage.read(), plugin_id)
    secure_scrubbed = (
        {k: v for k, v in existing_secret.items() if k not in non_sensitive} if existing_secret else None
    )
    need_secure_scrub = (
        secure_scrubbed is not None and existing_secret is not None and len(secure_scrubbed) != len(existing_secret)
    )
    if sensitive or need_secure_scrub:
        existing = storage.read() or {}
        plugin_secrets = existing.get("pluginSecrets")
        if not isinstance(plugin_secrets, dict):
            plugin_secrets = {}
            existing["pluginSecrets"] = plugin_secrets
        plugin_secrets[plugin_id] = {**(secure_scrubbed or {}), **sensitive}
        result = storage.update(existing)
        if not result.success:
            message = f"Failed to save sensitive plugin options for {plugin_id} to secure storage"
            logger.error(message)
            raise RuntimeError(message)
        if result.warning:
            logger.warning("Plugin secrets save warning: %s", result.warning)

    existing_in_settings = _settings_options(get_initial_settings(locations), plugin_id)
    keys_to_scrub = [k for k in existing_in_settings if k in sensitive]
    if non_sensitive or keys_to_scrub:
        options: dict[str, Any] = {**non_sensitive, **{k: None for k in keys_to_scrub}}
        error = update_settings_for_source(
            PermissionRuleSource.USER_SETTINGS, locations, {"pluginConfigs": {plugin_id: {"options": options}}}
        )
        if error is not None:
            logger.error(error)
            raise RuntimeError(f"Failed to save plugin options for {plugin_id}: {error}")

    clear_plugin_options_cache()


def delete_plugin_options(plugin_id: str, locations: SettingsLocations) -> None:
    """
    `deletePluginOptions`: drop `pluginConfigs[pluginId]` from user settings and the plugin's
    secrets (including the per-server `pluginId/server` keys). Best-effort: failures are logged.
    """
    # A JS object, even `{}`, is truthy: only an absent, null or falsy scalar entry is skipped.
    entry = _plugin_configs(get_initial_settings(locations)).get(plugin_id)
    if isinstance(entry, dict) or entry:
        error = update_settings_for_source(
            PermissionRuleSource.USER_SETTINGS, locations, {"pluginConfigs": {plugin_id: None}}
        )
        if error is not None:
            logger.warning("deletePluginOptions: failed to clear settings.pluginConfigs[%s]: %s", plugin_id, error)

    storage = get_secure_storage()
    existing = storage.read()
    secrets = existing.get("pluginSecrets") if existing else None
    if existing is not None and isinstance(secrets, dict) and secrets:
        prefix = f"{plugin_id}/"
        surviving = {k: v for k, v in secrets.items() if k != plugin_id and not k.startswith(prefix)}
        if len(surviving) != len(secrets):
            # `pluginSecrets: undefined` when none survive: the key is dropped from the document.
            updated = {k: v for k, v in existing.items() if k != "pluginSecrets"}
            if surviving:
                updated["pluginSecrets"] = surviving
            result = storage.update(updated)
            if not result.success:
                logger.warning("deletePluginOptions: failed to clear pluginSecrets for %s from keychain", plugin_id)

    clear_plugin_options_cache()


def get_unconfigured_options(plugin: LoadedPlugin, locations: SettingsLocations) -> PluginOptionSchema:
    """`getUnconfiguredOptions`: the `userConfig` fields whose saved values fail validation."""
    manifest_schema = plugin.manifest.get("userConfig")
    if not manifest_schema:
        return {}
    saved = load_plugin_options(get_plugin_storage_id(plugin), locations)
    if validate_user_config(saved, manifest_schema).valid:
        return {}
    unconfigured: PluginOptionSchema = {}
    for key, field_schema in manifest_schema.items():
        single_values = {key: saved[key]} if key in saved else {}
        if not validate_user_config(single_values, {key: field_schema}).valid:
            unconfigured[key] = field_schema
    return unconfigured


def _normalize(path: str) -> str:
    return path.replace("\\", "/") if sys.platform == "win32" else path


def substitute_plugin_variables(value: str, plugin: PluginPathLike) -> str:
    """
    `substitutePluginVariables`: `${CLAUDE_PLUGIN_ROOT}` -> the plugin path; `${CLAUDE_PLUGIN_DATA}`
    -> its data directory (created only when referenced). Without a `source`, the data reference
    stays literal.
    """
    plugin_path = plugin.path
    out = _PLUGIN_ROOT.sub(lambda _m: _normalize(plugin_path), value)
    if plugin.source:
        source = plugin.source
        out = _PLUGIN_DATA.sub(lambda _m: _normalize(get_plugin_data_dir(source)), out)
    return out


def substitute_user_config_variables(value: str, user_config: Mapping[str, Any]) -> str:
    """`substituteUserConfigVariables`: `${user_config.KEY}` -> the value; a missing key raises."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in user_config:
            raise ValueError(
                f"Missing required user configuration value: {key}. "
                "This should have been validated before variable substitution."
            )
        return _js_string(user_config[key])

    return _USER_CONFIG.sub(replace, value)


def substitute_user_config_in_content(
    content: str, options: Mapping[str, Any], schema: Mapping[str, Mapping[str, Any]]
) -> str:
    """
    `substituteUserConfigInContent`: the prose variant. Sensitive keys become a placeholder (no
    secrets in the model's context); unknown keys stay literal.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if (schema.get(key) or {}).get("sensitive") is True:
            return f"[sensitive option '{key}' not available in skill content]"
        if key not in options:
            return match.group(0)
        return _js_string(options[key])

    return _USER_CONFIG.sub(replace, content)


__all__ = [
    "PluginOptionSchema",
    "PluginOptionValues",
    "PluginPathLike",
    "clear_plugin_options_cache",
    "delete_plugin_options",
    "get_plugin_storage_id",
    "get_unconfigured_options",
    "load_plugin_options",
    "save_plugin_options",
    "substitute_plugin_variables",
    "substitute_user_config_in_content",
    "substitute_user_config_variables",
]
