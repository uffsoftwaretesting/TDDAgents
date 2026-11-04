"""
The plugin keys of the settings schema.

Ported from `reference/claude-code/src/utils/settings/types.ts` -> `ExtraKnownMarketplaceSchema` and the
`SettingsSchema` fields `enabledPlugins`, `extraKnownMarketplaces` (with its key = `source.name`
check for settings-sourced marketplaces), `strictKnownMarketplaces`, `blockedMarketplaces`,
`pluginConfigs` and `pluginTrustMessage`.

Upstream validates a whole settings file against `SettingsSchema`, and a failure ignores the file
(`parseSettingsFileUncached` yields `settings: null`); so does this build, against `settings_json_schema`
below (`app/utils/settings/schema_validator.py` -> `safe_parse_settings`). The plugin keys are checked by
their own port here: `parse_plugin_settings` lists the issues and the parsed keys (unknown keys of
nested objects stripped, as Zod returns them), and `safe_parse_settings` reports them with the rest.

`settings_json_schema` is `SettingsSchema` (with `PermissionsSchema`, `AllowedMcpServerEntrySchema`,
`DeniedMcpServerEntrySchema`, `ExtraKnownMarketplaceSchema` and `CUSTOMIZATION_SURFACES`) as Zod's
`toJSONSchema` prints it, **limited to the keys this build reads** (`SUPPORTED_SETTINGS_KEYS`), each
with upstream's `.describe()` text after the product substitutions ("Claude Code" -> "TDDAgents",
`.claude/` -> `.tddagents/`, the plugin folder `.claude-plugin/` -> `.tddagents-plugin/`). It is the
"Full Settings JSON Schema" the `update-config` skill prints (`app/utils/settings/schema_output.py`).
Upstream's other keys (`env`, `model`, `attribution`, `statusLine`, ...) are left out: a key
this build does not read would be documented but do nothing.
`permissions.defaultMode` lists this build's modes (`PERMISSION_MODES`, `auto` included, as upstream's
`TRANSCRIPT_CLASSIFIER` build); `permissions.disableAutoMode` is not read here and is left out.
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


#: `CUSTOMIZATION_SURFACES`: what `strictPluginOnlyCustomization` can lock.
CUSTOMIZATION_SURFACES: tuple[str, ...] = ("skills", "agents", "hooks", "mcp")

#: The top-level settings keys this build reads, in `SettingsSchema`'s declaration order.
SUPPORTED_SETTINGS_KEYS: tuple[str, ...] = (
    "permissions",
    "enableAllProjectMcpServers",
    "enabledMcpjsonServers",
    "disabledMcpjsonServers",
    "allowedMcpServers",
    "deniedMcpServers",
    "hooks",
    "disableAllHooks",
    "allowManagedHooksOnly",
    "allowedHttpHookUrls",
    "httpHookAllowedEnvVars",
    "allowManagedPermissionRulesOnly",
    "allowManagedMcpServersOnly",
    "strictPluginOnlyCustomization",
    "enabledPlugins",
    "extraKnownMarketplaces",
    "strictKnownMarketplaces",
    "blockedMarketplaces",
    "alwaysThinkingEnabled",
    "pluginConfigs",
    "plansDirectory",
    "autoMemoryEnabled",
    "autoMemoryDirectory",
    "skipDangerousModePermissionPrompt",
    "pluginTrustMessage",
)


def _mcp_server_entry_json_schema(name: str, command: str, url: str) -> dict[str, Any]:
    """`AllowedMcpServerEntrySchema` / `DeniedMcpServerEntrySchema` (the exactly-one `.refine` is not JSON Schema)."""
    from app.utils.settings.schema_builder import array, describe, obj, opt, string

    return obj({
        "serverName": opt(describe(string(pattern="^[a-zA-Z0-9_-]+$"), name)),
        "serverCommand": opt(describe(array(string(), min_items=1), command)),
        "serverUrl": opt(describe(string(), url)),
    })


def permissions_json_schema() -> dict[str, Any]:
    """`PermissionsSchema()` as JSON Schema (`PermissionRuleSchema`'s `.superRefine` is not JSON Schema)."""
    from app.types.permissions import PERMISSION_MODES
    from app.utils.settings.schema_builder import array, describe, enum, obj, opt, string

    return obj({
        "allow": opt(describe(array(string()), "List of permission rules for allowed operations")),
        "deny": opt(describe(array(string()), "List of permission rules for denied operations")),
        "ask": opt(describe(array(string()), "List of permission rules that should always prompt for confirmation")),
        "defaultMode": opt(describe(enum([str(mode) for mode in PERMISSION_MODES]),
                                    "Default permission mode when TDDAgents needs access")),
        "disableBypassPermissionsMode": opt(describe(enum(["disable"]),
                                                     "Disable the ability to bypass permission prompts")),
        "additionalDirectories": opt(describe(array(string()),
                                              "Additional directories to include in the permission scope")),
    }, passthrough=True)


def settings_json_schema() -> dict[str, Any]:
    """`SettingsSchema()` as `toJSONSchema(..., {io: 'input'})` prints it, for `SUPPORTED_SETTINGS_KEYS`."""
    from app.schemas.hooks import hooks_json_schema
    from app.utils.plugins.schemas import marketplace_source_json_schema
    from app.utils.settings.schema_builder import (
        array,
        boolean,
        describe,
        document,
        enum,
        number,
        obj,
        opt,
        record,
        string,
        union,
    )

    option_value = union(string(), number(), boolean(), array(string()))
    extra_known_marketplace = obj({
        "source": (describe(marketplace_source_json_schema(), "Where to fetch the marketplace from"), True),
        "installLocation": opt(describe(string(), (
            "Local cache path where marketplace manifest is stored (auto-generated if not provided)"))),
        "autoUpdate": opt(describe(boolean(), (
            "Whether to automatically update this marketplace and its installed plugins on startup"))),
    })
    plugin_config = obj({
        "mcpServers": opt(describe(record(record(option_value)),
                                   "User configuration values for MCP servers keyed by server name")),
        "options": opt(describe(record(option_value), (
            "Non-sensitive option values from plugin manifest userConfig, keyed by option name. Sensitive values "
            "go to secure storage instead."))),
    })
    properties = {
        "permissions": opt(describe(permissions_json_schema(), "Tool usage permissions configuration")),
        "enableAllProjectMcpServers": opt(describe(boolean(), (
            "Whether to automatically approve all MCP servers in the project"))),
        "enabledMcpjsonServers": opt(describe(array(string()), "List of approved MCP servers from .mcp.json")),
        "disabledMcpjsonServers": opt(describe(array(string()), "List of rejected MCP servers from .mcp.json")),
        "allowedMcpServers": opt(describe(array(_mcp_server_entry_json_schema(
            "Name of the MCP server that users are allowed to configure",
            "Command array [command, ...args] to match exactly for allowed stdio servers",
            'URL pattern with wildcard support (e.g., "https://*.example.com/*") for allowed remote MCP servers',
        )), (
            "Enterprise allowlist of MCP servers that can be used. Applies to all scopes including enterprise "
            "servers from managed-mcp.json. If undefined, all servers are allowed. If empty array, no servers are "
            "allowed. Denylist takes precedence - if a server is on both lists, it is denied."))),
        "deniedMcpServers": opt(describe(array(_mcp_server_entry_json_schema(
            "Name of the MCP server that is explicitly blocked",
            "Command array [command, ...args] to match exactly for blocked stdio servers",
            'URL pattern with wildcard support (e.g., "https://*.example.com/*") for blocked remote MCP servers',
        )), (
            "Enterprise denylist of MCP servers that are explicitly blocked. If a server is on the denylist, it "
            "will be blocked across all scopes including enterprise. Denylist takes precedence over allowlist - if "
            "a server is on both lists, it is denied."))),
        "hooks": opt(describe(hooks_json_schema(), "Custom commands to run before/after tool executions")),
        "disableAllHooks": opt(describe(boolean(), "Disable all hooks and statusLine execution")),
        "allowManagedHooksOnly": opt(describe(boolean(), (
            "When true (and set in managed settings), only hooks from managed settings run. User, project, and "
            "local hooks are ignored."))),
        "allowedHttpHookUrls": opt(describe(array(string()), (
            "Allowlist of URL patterns that HTTP hooks may target. Supports * as a wildcard (e.g. "
            '"https://hooks.example.com/*"). When set, HTTP hooks with non-matching URLs are blocked. If undefined, '
            "all URLs are allowed. If empty array, no HTTP hooks are allowed. Arrays merge across settings sources "
            "(same semantics as allowedMcpServers)."))),
        "httpHookAllowedEnvVars": opt(describe(array(string()), (
            "Allowlist of environment variable names HTTP hooks may interpolate into headers. When set, each "
            "hook's effective allowedEnvVars is the intersection with this list. If undefined, no restriction is "
            "applied. Arrays merge across settings sources (same semantics as allowedMcpServers)."))),
        "allowManagedPermissionRulesOnly": opt(describe(boolean(), (
            "When true (and set in managed settings), only permission rules (allow/deny/ask) from managed "
            "settings are respected. User, project, local, and CLI argument permission rules are ignored."))),
        "allowManagedMcpServersOnly": opt(describe(boolean(), (
            "When true (and set in managed settings), allowedMcpServers is only read from managed settings. "
            "deniedMcpServers still merges from all sources, so users can deny servers for themselves. Users can "
            "still add their own MCP servers, but only the admin-defined allowlist applies."))),
        "strictPluginOnlyCustomization": opt(describe(union(boolean(), array(enum(CUSTOMIZATION_SURFACES))), (
            "When set in managed settings, blocks non-plugin customization sources for the listed surfaces. Array "
            'form locks specific surfaces (e.g. ["skills", "hooks"]); `true` locks all four; `false` is an explicit '
            "no-op. Blocked: ~/.tddagents/{surface}/, .tddagents/{surface}/ (project), settings.json hooks, "
            ".mcp.json. NOT blocked: managed (policySettings) sources, plugin-provided customizations. Composes "
            "with strictKnownMarketplaces for end-to-end admin control — plugins gated by marketplace allowlist, "
            "everything else blocked here."))),
        "enabledPlugins": opt(describe(record(union(array(string()), boolean())), (
            'Enabled plugins using plugin-id@marketplace-id format. Example: { "formatter@anthropic-tools": true }. '
            "Also supports extended format with version constraints."))),
        "extraKnownMarketplaces": opt(describe(record(extra_known_marketplace), (
            "Additional marketplaces to make available for this repository. Typically used in repository "
            ".tddagents/settings.json to ensure team members have required plugin sources."))),
        "strictKnownMarketplaces": opt(describe(array(marketplace_source_json_schema()), (
            "Enterprise strict list of allowed marketplace sources. When set in managed settings, ONLY these exact "
            "sources can be added as marketplaces. The check happens BEFORE downloading, so blocked sources never "
            "touch the filesystem. Note: this is a policy gate only — it does NOT register marketplaces. To "
            "pre-register allowed marketplaces for users, also set extraKnownMarketplaces."))),
        "blockedMarketplaces": opt(describe(array(marketplace_source_json_schema()), (
            "Enterprise blocklist of marketplace sources. When set in managed settings, these exact sources are "
            "blocked from being added as marketplaces. The check happens BEFORE downloading, so blocked sources "
            "never touch the filesystem."))),
        "alwaysThinkingEnabled": opt(describe(boolean(), (
            "When false, thinking is disabled. When absent or true, thinking is enabled automatically for "
            "supported models."))),
        "pluginConfigs": opt(describe(record(plugin_config), (
            "Per-plugin configuration including MCP server user configs, keyed by plugin ID "
            "(plugin@marketplace format)"))),
        "plansDirectory": opt(describe(string(), (
            "Custom directory for plan files, relative to project root. If not set, defaults to "
            "~/.tddagents/plans/"))),
        "autoMemoryEnabled": opt(describe(boolean(), (
            "Enable auto-memory for this project. When false, TDDAgents will not read from or write to the "
            "auto-memory directory."))),
        "autoMemoryDirectory": opt(describe(string(), (
            "Custom directory path for auto-memory storage. Supports ~/ prefix for home directory expansion. "
            "Ignored if set in projectSettings (checked-in .tddagents/settings.json) for security. When unset, "
            "defaults to ~/.tddagents/projects/<sanitized-cwd>/memory/."))),
        "skipDangerousModePermissionPrompt": opt(describe(boolean(), (
            "Whether the user has accepted the bypass permissions mode dialog"))),
        "pluginTrustMessage": opt(describe(string(), (
            "Custom message to append to the plugin trust warning shown before installation. Only read from "
            "policy settings (managed-settings.json / MDM). Useful for enterprise administrators to add "
            'organization-specific context (e.g., "All plugins from our internal marketplace are vetted and '
            'approved.").'))),
    }
    assert tuple(properties) == SUPPORTED_SETTINGS_KEYS
    return document(obj(properties, passthrough=True))


__all__ = [
    "CUSTOMIZATION_SURFACES",
    "SUPPORTED_SETTINGS_KEYS",
    "permissions_json_schema",
    "settings_json_schema",
    "PLUGIN_SETTINGS_KEYS",
    "ExtraKnownMarketplace",
    "PluginConfig",
    "PluginSettings",
    "SettingsPluginKeysSchema",
    "parse_plugin_settings",
    "plugin_settings_schema_errors",
]
