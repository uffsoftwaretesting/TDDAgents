"""
The plugin keys of the settings schema.

Ported from `reference/claude-code/src/utils/settings/types.ts` -> `ExtraKnownMarketplaceSchema` and the
`SettingsSchema` fields `enabledPlugins`, `extraKnownMarketplaces` (with its key = `source.name`
check for settings-sourced marketplaces), `strictKnownMarketplaces`, `blockedMarketplaces`,
`pluginConfigs` and `pluginTrustMessage`.

Upstream validates a whole settings file against `SettingsSchema`, and a failure ignores the file
(`parseSettingsFileUncached` yields `settings: null`). This repository validates only some blocks
(the `hooks` block, `app/schemas/hooks.py`, and these plugin keys). `parse_plugin_settings` lists
the issues and the parsed keys: `parse_settings_file` ignores a file that has any issue and
otherwise keeps the parsed keys (unknown keys of nested objects stripped, as Zod returns them).
Every other key stays as leniently read as before.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypeAlias, TypedDict

from pydantic import ConfigDict, Field, with_config

from app.utils.plugins.schemas import (
    MarketplaceSource,
    Schema,
    format_issues,
    safe_parse,
)

_MarketplaceSourceField: TypeAlias = Annotated[MarketplaceSource, Field(discriminator="source")]


@with_config(ConfigDict(strict=True))
class ExtraKnownMarketplace(TypedDict):
    """`ExtraKnownMarketplaceSchema`: a known marketplace without `lastUpdated`."""

    source: _MarketplaceSourceField
    installLocation: NotRequired[str]
    autoUpdate: NotRequired[bool]


#: `z.union([z.string(), z.number(), z.boolean(), z.array(z.string())])`; an integer stays an int.
_OptionValue: TypeAlias = str | int | float | bool | list[str]


@with_config(ConfigDict(strict=True))
class PluginConfig(TypedDict):
    """One `pluginConfigs` entry."""

    mcpServers: NotRequired[dict[str, dict[str, _OptionValue]]]
    options: NotRequired[dict[str, _OptionValue]]


@with_config(ConfigDict(strict=True))
class PluginSettings(TypedDict):
    """The plugin keys of `SettingsSchema` (the rest of the document passes through)."""

    # `z.undefined()` is the in-memory deletion marker: JSON `null` is not accepted.
    enabledPlugins: NotRequired[dict[str, list[str] | bool]]
    extraKnownMarketplaces: NotRequired[dict[str, ExtraKnownMarketplace]]
    strictKnownMarketplaces: NotRequired[list[_MarketplaceSourceField]]
    blockedMarketplaces: NotRequired[list[_MarketplaceSourceField]]
    pluginConfigs: NotRequired[dict[str, PluginConfig]]
    pluginTrustMessage: NotRequired[str]


#: The settings keys this module validates.
PLUGIN_SETTINGS_KEYS: frozenset[str] = frozenset(
    PluginSettings.__required_keys__ | PluginSettings.__optional_keys__
)

SettingsPluginKeysSchema: Schema[PluginSettings] = Schema("PluginSettings", PluginSettings)


def _extra_known_marketplace_name_issues(value: Any) -> list[str]:
    """`extraKnownMarketplaces`'s `.check`: a settings source's name must equal its key."""
    if not isinstance(value, dict):
        return []
    issues: list[str] = []
    for key, entry in value.items():
        source = entry.get("source") if isinstance(entry, dict) else None
        if isinstance(source, dict) and source.get("source") == "settings" and source.get("name") != key:
            issues.append(
                f"extraKnownMarketplaces.{key}.source.name: Settings-sourced marketplace name must match its "
                f'extraKnownMarketplaces key (got key "{key}" but source.name "{source.get("name")}")'
            )
    return issues


def parse_plugin_settings(data: Any) -> tuple[list[str], dict[str, Any]]:
    """
    The plugin keys of a settings document: their schema issues (each `path: message`, empty when
    valid) and, when valid, the parsed keys.
    """
    if not isinstance(data, dict):
        return [], {}
    present = {k: data[k] for k in PLUGIN_SETTINGS_KEYS if k in data}
    if not present:
        return [], {}
    result = safe_parse(SettingsPluginKeysSchema, present)
    errors = [] if result.success else [format_issues([issue]) for issue in result.issues]
    # The record's `.check` reads only the record itself, so it reports alongside other issues.
    errors.extend(_extra_known_marketplace_name_issues(present.get("extraKnownMarketplaces")))
    if errors or result.data is None:
        return errors, {}
    return [], dict(result.data)


def plugin_settings_schema_errors(data: Any) -> list[str]:
    """The plugin keys' schema issues in a settings document, each as `path: message`; empty when valid."""
    return parse_plugin_settings(data)[0]


__all__ = [
    "PLUGIN_SETTINGS_KEYS",
    "ExtraKnownMarketplace",
    "PluginConfig",
    "PluginSettings",
    "SettingsPluginKeysSchema",
    "parse_plugin_settings",
    "plugin_settings_schema_errors",
]
