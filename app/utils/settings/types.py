"""
The settings schema, and its plugin keys.

Ported from `reference/claude-code/src/utils/settings/types.ts` -> `SettingsSchema`, `PermissionsSchema`,
`EnvironmentVariablesSchema`, `AllowedMcpServerEntrySchema`, `DeniedMcpServerEntrySchema`,
`ExtraKnownMarketplaceSchema`, `CUSTOMIZATION_SURFACES` (the plugin fields `enabledPlugins`,
`extraKnownMarketplaces` with its key = `source.name` check for settings-sourced marketplaces,
`strictKnownMarketplaces`, `blockedMarketplaces`, `pluginConfigs` and `pluginTrustMessage` also as
validators); `src/entrypoints/sandboxTypes.ts` -> `SandboxSettingsSchema`; `utils/settings/constants.ts` ->
`CLAUDE_CODE_SETTINGS_SCHEMA_URL`.

Upstream validates a whole settings file against `SettingsSchema`, and a failure ignores the file
(`parseSettingsFileUncached` yields `settings: null`); so does this build, against `settings_json_schema`
below (`app/utils/settings/schema_validator.py` -> `safe_parse_settings`). The plugin keys are checked by
their own port here: `parse_plugin_settings` lists the issues and the parsed keys (unknown keys of
nested objects stripped, as Zod returns them), and `safe_parse_settings` reports them with the rest.

`settings_json_schema` is `SettingsSchema` (with `PermissionsSchema`, `SandboxSettingsSchema` from
`entrypoints/sandboxTypes.ts`, `AllowedMcpServerEntrySchema`, `DeniedMcpServerEntrySchema`,
`ExtraKnownMarketplaceSchema`, `EnvironmentVariablesSchema` and `CUSTOMIZATION_SURFACES`) as Zod's
`toJSONSchema` prints it: **every key upstream declares** (`SETTINGS_KEYS`, user decision 2026-11-04), each
with its type and upstream's `.describe()` text after the product substitutions ("Claude Code" / "Claude"
-> "TDDAgents", `CLAUDE.md` -> `TDDAGENTS.md`, `.claude/` -> `.tddagents/`, the plugin folder
`.claude-plugin/` -> `.tddagents-plugin/`, `claude ssh` -> `CLI_COMMAND ssh`). It is the "Full Settings
JSON Schema" the `update-config` skill prints and an Edit refusal shows
(`app/utils/settings/schema_output.py`). A key this build does not act on (`UNREAD_UPSTREAM_SETTINGS_KEYS`:
`env`, `model`, `statusLine`, ...) is declared and its value checked, so a wrongly typed `model` makes the
file invalid as a whole, as upstream; nothing reads it.

The external build's gates: `permissions.defaultMode` lists this build's modes (`PERMISSION_MODES`, `auto`
included, as upstream's `TRANSCRIPT_CLASSIFIER` build), whose keys (`permissions.disableAutoMode`,
`skipAutoPermissionPrompt`, `useAutoModeDuringPlan`, `autoMode` without the ant-only `deny` alias) are
declared; `effortLevel` is the external enum. The keys behind other build or environment gates are declared
when their code is in the reference snapshot, and left out otherwise (`GATED_OFF_SETTINGS_KEYS`). Text and
values that name upstream's own services stay as written: `$schema`'s schemastore URL, the `claudeai`
login method and "Claude Pro/Max", the `claude-cli://` protocol, the `claude/channel` MCP capability.
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

#: `CLAUDE_CODE_SETTINGS_SCHEMA_URL` (`utils/settings/constants.ts`): the one value `$schema` accepts.
SETTINGS_SCHEMA_URL = "https://json.schemastore.org/claude-code-settings.json"

#: `SettingsSchema`'s top-level keys, in its declaration order: the external build's, its
#: `TRANSCRIPT_CLASSIFIER` keys (as `auto` mode is here), and the build- or environment-gated keys whose
#: code is in the reference snapshot (`xaaIdp`: `services/mcp/xaaIdpLogin.ts`, `commands/mcp/xaaIdpCommand.ts`;
#: `disableDeepLinkRegistration`: `utils/deepLink/registerProtocol.ts`; `voiceEnabled`: `commands/voice/`,
#: `services/voice.ts`; `defaultView`: `tools/BriefTool/BriefTool.ts`, read in `main.tsx`).
SETTINGS_KEYS: tuple[str, ...] = (
    "$schema",
    "apiKeyHelper",
    "awsCredentialExport",
    "awsAuthRefresh",
    "gcpAuthRefresh",
    "xaaIdp",
    "fileSuggestion",
    "respectGitignore",
    "cleanupPeriodDays",
    "env",
    "attribution",
    "includeCoAuthoredBy",
    "includeGitInstructions",
    "permissions",
    "model",
    "availableModels",
    "modelOverrides",
    "enableAllProjectMcpServers",
    "enabledMcpjsonServers",
    "disabledMcpjsonServers",
    "allowedMcpServers",
    "deniedMcpServers",
    "hooks",
    "worktree",
    "disableAllHooks",
    "defaultShell",
    "allowManagedHooksOnly",
    "allowedHttpHookUrls",
    "httpHookAllowedEnvVars",
    "allowManagedPermissionRulesOnly",
    "allowManagedMcpServersOnly",
    "strictPluginOnlyCustomization",
    "statusLine",
    "enabledPlugins",
    "extraKnownMarketplaces",
    "strictKnownMarketplaces",
    "blockedMarketplaces",
    "forceLoginMethod",
    "forceLoginOrgUUID",
    "otelHeadersHelper",
    "outputStyle",
    "language",
    "skipWebFetchPreflight",
    "sandbox",
    "feedbackSurveyRate",
    "spinnerTipsEnabled",
    "spinnerVerbs",
    "spinnerTipsOverride",
    "syntaxHighlightingDisabled",
    "terminalTitleFromRename",
    "alwaysThinkingEnabled",
    "effortLevel",
    "advisorModel",
    "fastMode",
    "fastModePerSessionOptIn",
    "promptSuggestionEnabled",
    "showClearContextOnPlanAccept",
    "agent",
    "companyAnnouncements",
    "pluginConfigs",
    "remote",
    "autoUpdatesChannel",
    "disableDeepLinkRegistration",
    "minimumVersion",
    "plansDirectory",
    "voiceEnabled",
    "channelsEnabled",
    "allowedChannelPlugins",
    "defaultView",
    "prefersReducedMotion",
    "autoMemoryEnabled",
    "autoMemoryDirectory",
    "autoDreamEnabled",
    "showThinkingSummaries",
    "skipDangerousModePermissionPrompt",
    "skipAutoPermissionPrompt",
    "useAutoModeDuringPlan",
    "autoMode",
    "disableAutoMode",
    "sshConfigs",
    "claudeMdExcludes",
    "pluginTrustMessage",
)

#: Upstream's gated keys whose code is absent from the reference snapshot, so this build does not declare
#: them (a settings file may still hold them: the top-level object is `.passthrough()`):
#: `classifierPermissionsEnabled` (ant-only; the external `utils/permissions/bashClassifier.ts` is a stub),
#: `minSleepDurationMs` / `maxSleepDurationMs` (`PROACTIVE` / `KAIROS`; `tools/SleepTool/` has only its
#: prompt, and nothing reads them), `assistant` / `assistantName` (`KAIROS`; `assistant/index.js` and
#: `assistant/gate.js`, which `main.tsx` requires, are absent, and nothing reads `assistantName`).
GATED_OFF_SETTINGS_KEYS: tuple[str, ...] = (
    "classifierPermissionsEnabled",
    "minSleepDurationMs",
    "maxSleepDurationMs",
    "assistant",
    "assistantName",
)

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
    "autoMode",
    "pluginTrustMessage",
)

#: The declared keys this build does not act on: their values are checked, but nothing reads them.
UNREAD_UPSTREAM_SETTINGS_KEYS: tuple[str, ...] = tuple(k for k in SETTINGS_KEYS if k not in SUPPORTED_SETTINGS_KEYS)

#: The top-level keys `SettingsSchema().strict()` accepts.
STRICT_SETTINGS_KEYS: frozenset[str] = frozenset(SETTINGS_KEYS)

#: `EnvironmentVariablesSchema`'s values are `z.coerce.string()`: any value is accepted and becomes `String(value)`.
COERCED_STRING_RECORD_KEYS: tuple[str, ...] = ("env",)

#: Keys declared `.catch(undefined)`: an invalid value drops the key instead of failing the document
#: (`strictPluginOnlyCustomization`'s preprocess runs first, `app/utils/settings/schema_validator.py`).
CATCH_SETTINGS_KEYS: tuple[str, ...] = ("strictPluginOnlyCustomization", "effortLevel")

#: `z.number().nonnegative().int()`: Zod's JSON Schema prints `.int()`'s safe-integer bounds in place of the
#: earlier `.nonnegative()`, which the parse still enforces (`app/utils/settings/schema_validator.py`).
NONNEGATIVE_INTEGER_SETTINGS_KEYS: tuple[str, ...] = ("cleanupPeriodDays",)


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
        "disableAutoMode": opt(describe(enum(["disable"]), "Disable auto mode")),
        "additionalDirectories": opt(describe(array(string()),
                                              "Additional directories to include in the permission scope")),
    }, passthrough=True)


def sandbox_json_schema() -> dict[str, Any]:
    """`SandboxSettingsSchema()` (`entrypoints/sandboxTypes.ts`, with its network and filesystem schemas)."""
    from app.utils.settings.schema_builder import array, boolean, describe, number, obj, opt, record, req, string

    network = obj({
        "allowedDomains": opt(array(string())),
        "allowManagedDomainsOnly": opt(describe(boolean(), (
            "When true (and set in managed settings), only allowedDomains and WebFetch(domain:...) allow rules from "
            "managed settings are respected. User, project, local, and flag settings domains are ignored. Denied "
            "domains are still respected from all sources."))),
        "allowUnixSockets": opt(describe(array(string()), (
            "macOS only: Unix socket paths to allow. Ignored on Linux (seccomp cannot filter by path)."))),
        "allowAllUnixSockets": opt(describe(boolean(), (
            "If true, allow all Unix sockets (disables blocking on both platforms)."))),
        "allowLocalBinding": opt(boolean()),
        "httpProxyPort": opt(number()),
        "socksProxyPort": opt(number()),
    })
    filesystem = obj({
        "allowWrite": opt(describe(array(string()), (
            "Additional paths to allow writing within the sandbox. Merged with paths from Edit(...) allow permission "
            "rules."))),
        "denyWrite": opt(describe(array(string()), (
            "Additional paths to deny writing within the sandbox. Merged with paths from Edit(...) deny permission "
            "rules."))),
        "denyRead": opt(describe(array(string()), (
            "Additional paths to deny reading within the sandbox. Merged with paths from Read(...) deny permission "
            "rules."))),
        "allowRead": opt(describe(array(string()), (
            "Paths to re-allow reading within denyRead regions. Takes precedence over denyRead for matching paths."))),
        "allowManagedReadPathsOnly": opt(describe(boolean(), (
            "When true (set in managed settings), only allowRead paths from policySettings are used."))),
    })
    return obj({
        "enabled": opt(boolean()),
        "failIfUnavailable": opt(describe(boolean(), (
            "Exit with an error at startup if sandbox.enabled is true but the sandbox cannot start (missing "
            "dependencies, unsupported platform, or platform not in enabledPlatforms). When false (default), a "
            "warning is shown and commands run unsandboxed. Intended for managed-settings deployments that require "
            "sandboxing as a hard gate."))),
        "autoAllowBashIfSandboxed": opt(boolean()),
        "allowUnsandboxedCommands": opt(describe(boolean(), (
            "Allow commands to run outside the sandbox via the dangerouslyDisableSandbox parameter. When false, the "
            "dangerouslyDisableSandbox parameter is completely ignored and all commands must run sandboxed. "
            "Default: true."))),
        "network": opt(network),
        "filesystem": opt(filesystem),
        "ignoreViolations": opt(record(array(string()))),
        "enableWeakerNestedSandbox": opt(boolean()),
        "enableWeakerNetworkIsolation": opt(describe(boolean(), (
            "macOS only: Allow access to com.apple.trustd.agent in the sandbox. Needed for Go-based CLI tools (gh, "
            "gcloud, terraform, etc.) to verify TLS certificates when using httpProxyPort with a MITM proxy and "
            "custom CA. **Reduces security** — opens a potential data exfiltration vector through the trustd "
            "service. Default: false"))),
        "excludedCommands": opt(array(string())),
        "ripgrep": opt(describe(obj({"command": req(string()), "args": opt(array(string()))}),
                                "Custom ripgrep configuration for bundled ripgrep support")),
    }, passthrough=True)


def settings_json_schema() -> dict[str, Any]:
    """`SettingsSchema()` as `toJSONSchema(..., {io: 'input'})` prints it."""
    from app.constants.product import CLI_COMMAND
    from app.schemas.hooks import hooks_json_schema
    from app.utils.plugins.schemas import marketplace_source_json_schema
    from app.utils.settings.schema_builder import (
        array,
        boolean,
        describe,
        document,
        enum,
        integer,
        literal,
        number,
        obj,
        opt,
        record,
        req,
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
    command_config = {"type": req(literal("command")), "command": req(string())}
    ssh_config = obj({
        "id": req(describe(string(), (
            "Unique identifier for this SSH config. Used to match configs across settings sources."))),
        "name": req(describe(string(), "Display name for the SSH connection")),
        "sshHost": req(describe(string(), (
            'SSH host in format "user@hostname" or "hostname", or a host alias from ~/.ssh/config'))),
        "sshPort": opt(describe(integer(), "SSH port (default: 22)")),
        "sshIdentityFile": opt(describe(string(), "Path to SSH identity file (private key)")),
        "startDirectory": opt(describe(string(), (
            "Default working directory on the remote host. Supports tilde expansion (e.g. ~/projects). If not "
            "specified, defaults to the remote user home directory. Can be overridden by the [dir] positional "
            f"argument in `{CLI_COMMAND} ssh <config> [dir]`."))),
    })
    properties = {
        "$schema": opt(describe(literal(SETTINGS_SCHEMA_URL), "JSON Schema reference for TDDAgents settings")),
        "apiKeyHelper": opt(describe(string(), "Path to a script that outputs authentication values")),
        "awsCredentialExport": opt(describe(string(), "Path to a script that exports AWS credentials")),
        "awsAuthRefresh": opt(describe(string(), "Path to a script that refreshes AWS authentication")),
        "gcpAuthRefresh": opt(describe(string(), (
            "Command to refresh GCP authentication (e.g., gcloud auth application-default login)"))),
        "xaaIdp": opt(describe(obj({
            "issuer": req(describe(string(url=True), "IdP issuer URL for OIDC discovery")),
            "clientId": req(describe(string(), "TDDAgents' client_id registered at the IdP")),
            "callbackPort": opt(describe(integer(positive=True), (
                "Fixed loopback callback port for the IdP OIDC login. Only needed if the IdP does not honor RFC "
                "8252 port-any matching."))),
        }), "XAA (SEP-990) IdP connection. Configure once; all XAA-enabled MCP servers reuse this.")),
        "fileSuggestion": opt(describe(obj(command_config), "Custom file suggestion configuration for @ mentions")),
        "respectGitignore": opt(describe(boolean(), (
            "Whether file picker should respect .gitignore files (default: true). Note: .ignore files are always "
            "respected."))),
        "cleanupPeriodDays": opt(describe(integer(), (
            "Number of days to retain chat transcripts (default: 30). Setting to 0 disables session persistence "
            "entirely: no transcripts are written and existing transcripts are deleted at startup."))),
        "env": opt(describe(record(string()), "Environment variables to set for TDDAgents sessions")),
        "attribution": opt(describe(obj({
            "commit": opt(describe(string(), (
                "Attribution text for git commits, including any trailers. Empty string hides attribution."))),
            "pr": opt(describe(string(), (
                "Attribution text for pull request descriptions. Empty string hides attribution."))),
        }), (
            "Customize attribution text for commits and PRs. Each field defaults to the standard TDDAgents "
            "attribution if not set."))),
        "includeCoAuthoredBy": opt(describe(boolean(), (
            "Deprecated: Use attribution instead. Whether to include TDDAgents' co-authored by attribution in "
            "commits and PRs (defaults to true)"))),
        "includeGitInstructions": opt(describe(boolean(), (
            "Include built-in commit and PR workflow instructions in TDDAgents' system prompt (default: true)"))),
        "permissions": opt(describe(permissions_json_schema(), "Tool usage permissions configuration")),
        "model": opt(describe(string(), "Override the default model used by TDDAgents")),
        "availableModels": opt(describe(array(string()), (
            'Allowlist of models that users can select. Accepts family aliases ("opus" allows any opus version), '
            'version prefixes ("opus-4-5" allows only that version), and full model IDs. If undefined, all models '
            "are available. If empty array, only the default model is available. Typically set in managed settings "
            "by enterprise administrators."))),
        "modelOverrides": opt(describe(record(string()), (
            'Override mapping from Anthropic model ID (e.g. "claude-opus-4-6") to provider-specific model ID (e.g. '
            "a Bedrock inference profile ARN). Typically set in managed settings by enterprise administrators."))),
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
        "worktree": opt(describe(obj({
            "symlinkDirectories": opt(describe(array(string()), (
                "Directories to symlink from main repository to worktrees to avoid disk bloat. Must be explicitly "
                "configured - no directories are symlinked by default. Common examples: \"node_modules\", "
                "\".cache\", \".bin\""))),
            "sparsePaths": opt(describe(array(string()), (
                "Directories to include when creating worktrees, via git sparse-checkout (cone mode). Dramatically "
                "faster in large monorepos — only the listed paths are written to disk."))),
        }), "Git worktree configuration for --worktree flag.")),
        "disableAllHooks": opt(describe(boolean(), "Disable all hooks and statusLine execution")),
        "defaultShell": opt(describe(enum(["bash", "powershell"]), (
            "Default shell for input-box ! commands. Defaults to 'bash' on all platforms (no Windows auto-flip)."))),
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
        "statusLine": opt(describe(obj({**command_config, "padding": opt(number())}),
                                   "Custom status line display configuration")),
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
        "forceLoginMethod": opt(describe(enum(["claudeai", "console"]), (
            'Force a specific login method: "claudeai" for Claude Pro/Max, "console" for Console billing'))),
        "forceLoginOrgUUID": opt(describe(string(), "Organization UUID to use for OAuth login")),
        "otelHeadersHelper": opt(describe(string(), "Path to a script that outputs OpenTelemetry headers")),
        "outputStyle": opt(describe(string(), "Controls the output style for assistant responses")),
        "language": opt(describe(string(), (
            'Preferred language for TDDAgents responses and voice dictation (e.g., "japanese", "spanish")'))),
        "skipWebFetchPreflight": opt(describe(boolean(), (
            "Skip the WebFetch blocklist check for enterprise environments with restrictive security policies"))),
        "sandbox": opt(sandbox_json_schema()),
        "feedbackSurveyRate": opt(describe(number(minimum=0, maximum=1), (
            "Probability (0–1) that the session quality survey appears when eligible. 0.05 is a reasonable "
            "starting point."))),
        "spinnerTipsEnabled": opt(describe(boolean(), "Whether to show tips in the spinner")),
        "spinnerVerbs": opt(describe(obj({
            "mode": req(enum(["append", "replace"])),
            "verbs": req(array(string())),
        }), 'Customize spinner verbs. mode: "append" adds verbs to defaults, "replace" uses only your verbs.')),
        "spinnerTipsOverride": opt(describe(obj({
            "excludeDefault": opt(boolean()),
            "tips": req(array(string())),
        }), (
            "Override spinner tips. tips: array of tip strings. excludeDefault: if true, only show custom tips "
            "(default: false)."))),
        "syntaxHighlightingDisabled": opt(describe(boolean(), "Whether to disable syntax highlighting in diffs")),
        "terminalTitleFromRename": opt(describe(boolean(), (
            "Whether /rename updates the terminal tab title (defaults to true). Set to false to keep auto-generated "
            "topic titles."))),
        "alwaysThinkingEnabled": opt(describe(boolean(), (
            "When false, thinking is disabled. When absent or true, thinking is enabled automatically for "
            "supported models."))),
        "effortLevel": opt(describe(enum(["low", "medium", "high"]), "Persisted effort level for supported models.")),
        "advisorModel": opt(describe(string(), "Advisor model for the server-side advisor tool.")),
        "fastMode": opt(describe(boolean(), (
            "When true, fast mode is enabled. When absent or false, fast mode is off."))),
        "fastModePerSessionOptIn": opt(describe(boolean(), (
            "When true, fast mode does not persist across sessions. Each session starts with fast mode off."))),
        "promptSuggestionEnabled": opt(describe(boolean(), (
            "When false, prompt suggestions are disabled. When absent or true, prompt suggestions are enabled."))),
        "showClearContextOnPlanAccept": opt(describe(boolean(), (
            'When true, the plan-approval dialog offers a "clear context" option. Defaults to false.'))),
        "agent": opt(describe(string(), (
            "Name of an agent (built-in or custom) to use for the main thread. Applies the agent's system prompt, "
            "tool restrictions, and model."))),
        "companyAnnouncements": opt(describe(array(string()), (
            "Company announcements to display at startup (one will be randomly selected if multiple are "
            "provided)"))),
        "pluginConfigs": opt(describe(record(plugin_config), (
            "Per-plugin configuration including MCP server user configs, keyed by plugin ID "
            "(plugin@marketplace format)"))),
        "remote": opt(describe(obj({
            "defaultEnvironmentId": opt(describe(string(), "Default environment ID to use for remote sessions")),
        }), "Remote session configuration")),
        "autoUpdatesChannel": opt(describe(enum(["latest", "stable"]), (
            "Release channel for auto-updates (latest or stable)"))),
        "disableDeepLinkRegistration": opt(describe(enum(["disable"]), (
            "Prevent claude-cli:// protocol handler registration with the OS"))),
        "minimumVersion": opt(describe(string(), (
            "Minimum version to stay on - prevents downgrades when switching to stable channel"))),
        "plansDirectory": opt(describe(string(), (
            "Custom directory for plan files, relative to project root. If not set, defaults to "
            "~/.tddagents/plans/"))),
        "voiceEnabled": opt(describe(boolean(), "Enable voice mode (hold-to-talk dictation)")),
        "channelsEnabled": opt(describe(boolean(), (
            "Teams/Enterprise opt-in for channel notifications (MCP servers with the claude/channel capability "
            "pushing inbound messages). Default off. Set true to allow; users then select servers via "
            "--channels."))),
        "allowedChannelPlugins": opt(describe(array(obj({
            "marketplace": req(string()),
            "plugin": req(string()),
        })), (
            "Teams/Enterprise allowlist of channel plugins. When set, replaces the default Anthropic allowlist — "
            "admins decide which plugins may push inbound messages. Undefined falls back to the default. Requires "
            "channelsEnabled: true."))),
        "defaultView": opt(describe(enum(["chat", "transcript"]), (
            "Default transcript view: chat (SendUserMessage checkpoints only) or transcript (full)"))),
        "prefersReducedMotion": opt(describe(boolean(), (
            "Reduce or disable animations for accessibility (spinner shimmer, flash effects, etc.)"))),
        "autoMemoryEnabled": opt(describe(boolean(), (
            "Enable auto-memory for this project. When false, TDDAgents will not read from or write to the "
            "auto-memory directory."))),
        "autoMemoryDirectory": opt(describe(string(), (
            "Custom directory path for auto-memory storage. Supports ~/ prefix for home directory expansion. "
            "Ignored if set in projectSettings (checked-in .tddagents/settings.json) for security. When unset, "
            "defaults to ~/.tddagents/projects/<sanitized-cwd>/memory/."))),
        "autoDreamEnabled": opt(describe(boolean(), (
            "Enable background memory consolidation (auto-dream). When set, overrides the server-side default."))),
        "showThinkingSummaries": opt(describe(boolean(), (
            "Show thinking summaries in the transcript view (ctrl+o). Default: false."))),
        "skipDangerousModePermissionPrompt": opt(describe(boolean(), (
            "Whether the user has accepted the bypass permissions mode dialog"))),
        "skipAutoPermissionPrompt": opt(describe(boolean(), (
            "Whether the user has accepted the auto mode opt-in dialog"))),
        "useAutoModeDuringPlan": opt(describe(boolean(), (
            "Whether plan mode uses auto mode semantics when auto mode is available (default: true)"))),
        "autoMode": opt(describe(obj({
            "allow": opt(describe(array(string()), "Rules for the auto mode classifier allow section")),
            "soft_deny": opt(describe(array(string()), "Rules for the auto mode classifier deny section")),
            "environment": opt(describe(array(string()), "Entries for the auto mode classifier environment section")),
        }), "Auto mode classifier prompt customization")),
        "disableAutoMode": opt(describe(enum(["disable"]), "Disable auto mode")),
        "sshConfigs": opt(describe(array(ssh_config), (
            "SSH connection configurations for remote environments. Typically set in managed settings by "
            "enterprise administrators to pre-configure SSH connections for team members."))),
        "claudeMdExcludes": opt(describe(array(string()), (
            "Glob patterns or absolute paths of TDDAGENTS.md files to exclude from loading. Patterns are matched "
            "against absolute file paths using picomatch. Only applies to User, Project, and Local memory types "
            '(Managed/policy files cannot be excluded). Examples: "/home/user/monorepo/TDDAGENTS.md", '
            '"**/code/TDDAGENTS.md", "**/some-dir/.tddagents/rules/**"'))),
        "pluginTrustMessage": opt(describe(string(), (
            "Custom message to append to the plugin trust warning shown before installation. Only read from "
            "policy settings (managed-settings.json / MDM). Useful for enterprise administrators to add "
            'organization-specific context (e.g., "All plugins from our internal marketplace are vetted and '
            'approved.").'))),
    }
    assert tuple(properties) == SETTINGS_KEYS
    return document(obj(properties, passthrough=True))


__all__ = [
    "CATCH_SETTINGS_KEYS",
    "COERCED_STRING_RECORD_KEYS",
    "CUSTOMIZATION_SURFACES",
    "GATED_OFF_SETTINGS_KEYS",
    "NONNEGATIVE_INTEGER_SETTINGS_KEYS",
    "SETTINGS_KEYS",
    "SETTINGS_SCHEMA_URL",
    "STRICT_SETTINGS_KEYS",
    "SUPPORTED_SETTINGS_KEYS",
    "UNREAD_UPSTREAM_SETTINGS_KEYS",
    "permissions_json_schema",
    "sandbox_json_schema",
    "settings_json_schema",
    "PLUGIN_SETTINGS_KEYS",
    "ExtraKnownMarketplace",
    "PluginConfig",
    "PluginSettings",
    "SettingsPluginKeysSchema",
    "parse_plugin_settings",
    "plugin_settings_schema_errors",
]
