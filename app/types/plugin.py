"""
Plugin types: a loaded plugin, the plugin error union and its display message.

Ported from `reference/claude-code/src/types/plugin.ts` -> `LoadedPlugin`, `PluginComponent`,
`PluginError`, `PluginLoadResult`, `getPluginErrorMessage`, `PluginRepository`, `PluginConfig`.

`LoadedPlugin` is a mutable dataclass (upstream mutates `enabled` while loading); each
`PluginError` variant is a `TypedDict` tagged by `type`, as upstream's discriminated union is a
plain object. `BuiltinPluginDefinition` belongs with the built-in plugin registry
(`src/plugins/builtinPlugins.ts`) and is not ported here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypeAlias, TypedDict

from app.utils.plugins.schemas import CommandMetadata, LspServerConfig, PluginManifest

#: `PluginComponent`.
PluginComponent: TypeAlias = Literal["commands", "agents", "skills", "hooks", "output-styles"]


@dataclass
class PluginRepository:
    """`PluginRepository`."""

    url: str
    branch: str
    last_updated: str | None = None
    commit_sha: str | None = None


@dataclass
class PluginConfig:
    """`PluginConfig`: the repositories a plugin configuration names."""

    repositories: dict[str, PluginRepository]


@dataclass
class LoadedPlugin:
    """`LoadedPlugin`. `source` is the plugin id (`name@marketplace`); `repository` repeats it."""

    name: str
    manifest: PluginManifest
    path: str
    source: str
    repository: str
    enabled: bool | None = None
    is_builtin: bool | None = None
    sha: str | None = None
    commands_path: str | None = None
    commands_paths: list[str] | None = None
    commands_metadata: dict[str, CommandMetadata] | None = None
    agents_path: str | None = None
    agents_paths: list[str] | None = None
    skills_path: str | None = None
    skills_paths: list[str] | None = None
    output_styles_path: str | None = None
    output_styles_paths: list[str] | None = None
    hooks_config: dict[str, Any] | None = None
    mcp_servers: dict[str, dict[str, Any]] | None = None
    lsp_servers: dict[str, LspServerConfig] | None = None
    settings: dict[str, Any] | None = None


class PathNotFoundError(TypedDict):
    type: Literal["path-not-found"]
    source: str
    plugin: NotRequired[str]
    path: str
    component: PluginComponent


class GitAuthFailedError(TypedDict):
    type: Literal["git-auth-failed"]
    source: str
    plugin: NotRequired[str]
    gitUrl: str
    authType: Literal["ssh", "https"]


class GitTimeoutError(TypedDict):
    type: Literal["git-timeout"]
    source: str
    plugin: NotRequired[str]
    gitUrl: str
    operation: Literal["clone", "pull"]


class NetworkError(TypedDict):
    type: Literal["network-error"]
    source: str
    plugin: NotRequired[str]
    url: str
    details: NotRequired[str]


class ManifestParseError(TypedDict):
    type: Literal["manifest-parse-error"]
    source: str
    plugin: NotRequired[str]
    manifestPath: str
    parseError: str


class ManifestValidationError(TypedDict):
    type: Literal["manifest-validation-error"]
    source: str
    plugin: NotRequired[str]
    manifestPath: str
    validationErrors: list[str]


class PluginNotFoundError(TypedDict):
    type: Literal["plugin-not-found"]
    source: str
    pluginId: str
    marketplace: str


class MarketplaceNotFoundError(TypedDict):
    type: Literal["marketplace-not-found"]
    source: str
    marketplace: str
    availableMarketplaces: list[str]


class MarketplaceLoadFailedError(TypedDict):
    type: Literal["marketplace-load-failed"]
    source: str
    marketplace: str
    reason: str


class McpConfigInvalidError(TypedDict):
    type: Literal["mcp-config-invalid"]
    source: str
    plugin: str
    serverName: str
    validationError: str


class McpServerSuppressedDuplicateError(TypedDict):
    type: Literal["mcp-server-suppressed-duplicate"]
    source: str
    plugin: str
    serverName: str
    duplicateOf: str


class LspConfigInvalidError(TypedDict):
    type: Literal["lsp-config-invalid"]
    source: str
    plugin: str
    serverName: str
    validationError: str


class HookLoadFailedError(TypedDict):
    type: Literal["hook-load-failed"]
    source: str
    plugin: str
    hookPath: str
    reason: str


class ComponentLoadFailedError(TypedDict):
    type: Literal["component-load-failed"]
    source: str
    plugin: str
    component: PluginComponent
    path: str
    reason: str


class McpbDownloadFailedError(TypedDict):
    type: Literal["mcpb-download-failed"]
    source: str
    plugin: str
    url: str
    reason: str


class McpbExtractFailedError(TypedDict):
    type: Literal["mcpb-extract-failed"]
    source: str
    plugin: str
    mcpbPath: str
    reason: str


class McpbInvalidManifestError(TypedDict):
    type: Literal["mcpb-invalid-manifest"]
    source: str
    plugin: str
    mcpbPath: str
    validationError: str


class LspServerStartFailedError(TypedDict):
    type: Literal["lsp-server-start-failed"]
    source: str
    plugin: str
    serverName: str
    reason: str


class LspServerCrashedError(TypedDict):
    type: Literal["lsp-server-crashed"]
    source: str
    plugin: str
    serverName: str
    exitCode: int | None
    signal: NotRequired[str]


class LspRequestTimeoutError(TypedDict):
    type: Literal["lsp-request-timeout"]
    source: str
    plugin: str
    serverName: str
    method: str
    timeoutMs: int


class LspRequestFailedError(TypedDict):
    type: Literal["lsp-request-failed"]
    source: str
    plugin: str
    serverName: str
    method: str
    error: str


class MarketplaceBlockedByPolicyError(TypedDict):
    type: Literal["marketplace-blocked-by-policy"]
    source: str
    plugin: NotRequired[str]
    marketplace: str
    blockedByBlocklist: NotRequired[bool]
    allowedSources: list[str]


class DependencyUnsatisfiedError(TypedDict):
    type: Literal["dependency-unsatisfied"]
    source: str
    plugin: str
    dependency: str
    reason: Literal["not-enabled", "not-found"]


class PluginCacheMissError(TypedDict):
    type: Literal["plugin-cache-miss"]
    source: str
    plugin: str
    installPath: str


class GenericError(TypedDict):
    type: Literal["generic-error"]
    source: str
    plugin: NotRequired[str]
    error: str


#: `PluginError`.
PluginError: TypeAlias = (
    PathNotFoundError
    | GitAuthFailedError
    | GitTimeoutError
    | NetworkError
    | ManifestParseError
    | ManifestValidationError
    | PluginNotFoundError
    | MarketplaceNotFoundError
    | MarketplaceLoadFailedError
    | McpConfigInvalidError
    | McpServerSuppressedDuplicateError
    | LspConfigInvalidError
    | HookLoadFailedError
    | ComponentLoadFailedError
    | McpbDownloadFailedError
    | McpbExtractFailedError
    | McpbInvalidManifestError
    | LspServerStartFailedError
    | LspServerCrashedError
    | LspRequestTimeoutError
    | LspRequestFailedError
    | MarketplaceBlockedByPolicyError
    | DependencyUnsatisfiedError
    | PluginCacheMissError
    | GenericError
)


@dataclass
class PluginLoadResult:
    """`PluginLoadResult`."""

    enabled: list[LoadedPlugin] = field(default_factory=list)
    disabled: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)


def get_plugin_error_message(error: PluginError) -> str:
    """`getPluginErrorMessage`: a display message for any plugin error."""
    e: dict[str, Any] = dict(error)
    kind = e["type"]
    if kind == "generic-error":
        return str(e["error"])
    if kind == "path-not-found":
        return f"Path not found: {e['path']} ({e['component']})"
    if kind == "git-auth-failed":
        return f"Git authentication failed ({e['authType']}): {e['gitUrl']}"
    if kind == "git-timeout":
        return f"Git {e['operation']} timeout: {e['gitUrl']}"
    if kind == "network-error":
        details = f" - {e['details']}" if e.get("details") else ""
        return f"Network error: {e['url']}{details}"
    if kind == "manifest-parse-error":
        return f"Manifest parse error: {e['parseError']}"
    if kind == "manifest-validation-error":
        return f"Manifest validation failed: {', '.join(e['validationErrors'])}"
    if kind == "plugin-not-found":
        return f"Plugin {e['pluginId']} not found in marketplace {e['marketplace']}"
    if kind == "marketplace-not-found":
        return f"Marketplace {e['marketplace']} not found"
    if kind == "marketplace-load-failed":
        return f"Marketplace {e['marketplace']} failed to load: {e['reason']}"
    if kind == "mcp-config-invalid":
        return f"MCP server {e['serverName']} invalid: {e['validationError']}"
    if kind == "mcp-server-suppressed-duplicate":
        duplicate_of = str(e["duplicateOf"])
        if duplicate_of.startswith("plugin:"):
            parts = duplicate_of.split(":")
            dup = f'server provided by plugin "{parts[1] if len(parts) > 1 else "?"}"'
        else:
            dup = f'already-configured "{duplicate_of}"'
        return f'MCP server "{e["serverName"]}" skipped — same command/URL as {dup}'
    if kind == "hook-load-failed":
        return f"Hook load failed: {e['reason']}"
    if kind == "component-load-failed":
        return f"{e['component']} load failed from {e['path']}: {e['reason']}"
    if kind == "mcpb-download-failed":
        return f"Failed to download MCPB from {e['url']}: {e['reason']}"
    if kind == "mcpb-extract-failed":
        return f"Failed to extract MCPB {e['mcpbPath']}: {e['reason']}"
    if kind == "mcpb-invalid-manifest":
        return f"MCPB manifest invalid at {e['mcpbPath']}: {e['validationError']}"
    if kind == "lsp-config-invalid":
        return (
            f'Plugin "{e["plugin"]}" has invalid LSP server config for "{e["serverName"]}": {e["validationError"]}'
        )
    if kind == "lsp-server-start-failed":
        return f'Plugin "{e["plugin"]}" failed to start LSP server "{e["serverName"]}": {e["reason"]}'
    if kind == "lsp-server-crashed":
        if e.get("signal"):
            return f'Plugin "{e["plugin"]}" LSP server "{e["serverName"]}" crashed with signal {e["signal"]}'
        exit_code = e.get("exitCode")
        shown = "unknown" if exit_code is None else exit_code
        return f'Plugin "{e["plugin"]}" LSP server "{e["serverName"]}" crashed with exit code {shown}'
    if kind == "lsp-request-timeout":
        return (
            f'Plugin "{e["plugin"]}" LSP server "{e["serverName"]}" timed out on {e["method"]} request after '
            f"{e['timeoutMs']}ms"
        )
    if kind == "lsp-request-failed":
        return f'Plugin "{e["plugin"]}" LSP server "{e["serverName"]}" {e["method"]} request failed: {e["error"]}'
    if kind == "marketplace-blocked-by-policy":
        if e.get("blockedByBlocklist"):
            return f"Marketplace '{e['marketplace']}' is blocked by enterprise policy"
        return f"Marketplace '{e['marketplace']}' is not in the allowed marketplace list"
    if kind == "dependency-unsatisfied":
        hint = (
            "disabled — enable it or remove the dependency"
            if e["reason"] == "not-enabled"
            else "not found in any configured marketplace"
        )
        return f'Dependency "{e["dependency"]}" is {hint}'
    if kind == "plugin-cache-miss":
        return f'Plugin "{e["plugin"]}" not cached at {e["installPath"]} — run /plugins to refresh'
    return str(e.get("error", kind))


__all__ = [
    "LoadedPlugin",
    "PluginComponent",
    "PluginConfig",
    "PluginError",
    "PluginLoadResult",
    "PluginRepository",
    "get_plugin_error_message",
]
