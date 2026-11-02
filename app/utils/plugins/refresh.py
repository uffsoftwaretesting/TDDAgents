"""
The layer-3 refresh: swap the active plugin components of the running session.

Ported from `reference/claude-code/src/utils/plugins/refresh.ts` -> `RefreshActivePluginsResult`,
`refreshActivePlugins`, `mergePluginErrors`, `errorKey`.

Three layers: intent (settings), materialization (`<config home>/plugins`, the reconciler) and
the active components (`AppState`, this module). Callers: `/reload-plugins`, the stream-json
`reload_plugins` control request (`print.ts`), and the background installation after a new
marketplace is installed (`app/services/plugins/plugin_installation_manager.py`).

`refresh_active_plugins(set_app_state, locations)` takes the run's `SettingsLocations`
(`app/cli/print.py` `run_settings_locations`), where upstream reads its process-wide settings.
The agent definitions are `get_agent_definitions_with_overrides` over the run's project and home
(this repository's `dict[str, AgentDefinition]`, upstream's `AgentDefinitionsResult.allAgents`).

**Stage-3 seams**: each component loader below (`_get_plugin_commands`,
`_load_plugin_mcp_servers`, `_load_plugin_lsp_servers`, `_load_plugin_hooks`,
`_reinitialize_lsp_server_manager`) is a placeholder returning nothing until stage 3 ports
`loadPluginCommands.ts`, `mcpPluginIntegration.ts`, `lspPluginIntegration.ts`,
`loadPluginHooks.ts` and the LSP manager; the plugin agents join
`get_agent_definitions_with_overrides` then (`loadPluginAgents.ts`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.state.app_state_store import AppState
from app.tools.agent_tool.load_agents_dir import AgentDefinition, get_agent_definitions_with_overrides
from app.types.plugin import LoadedPlugin, PluginError
from app.utils.plugins.cache_utils import clear_all_caches
from app.utils.plugins.orphaned_plugin_filter import clear_plugin_cache_exclusions
from app.utils.plugins.plugin_loader import load_all_plugins
from app.utils.settings.settings import SettingsLocations

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `SetAppState`.
SetAppState = Callable[[Callable[[AppState], AppState]], None]


@dataclass(frozen=True)
class RefreshActivePluginsResult:
    """`RefreshActivePluginsResult`: the counts, and the refreshed agents and plugin commands."""

    enabled_count: int
    disabled_count: int
    command_count: int
    agent_count: int
    hook_count: int
    mcp_count: int
    lsp_count: int
    error_count: int
    #: Upstream `agentDefinitions`: for callers that keep their own reference (print.ts).
    agent_definitions: dict[str, AgentDefinition] = field(default_factory=dict)
    #: Upstream `pluginCommands`.
    plugin_commands: list[Any] = field(default_factory=list)


# ── stage-3 seams ─────────────────────────────────────────────────────────────

async def _get_plugin_commands(locations: SettingsLocations) -> list[Any]:
    """Stage-3 seam: `getPluginCommands` (`loadPluginCommands.ts`). No plugin command is loaded yet."""
    return []


async def _load_plugin_mcp_servers(
    plugin: LoadedPlugin, errors: list[PluginError], locations: SettingsLocations
) -> dict[str, dict[str, Any]] | None:
    """Stage-3 seam: `loadPluginMcpServers` (`mcpPluginIntegration.ts`). No plugin MCP server is loaded yet."""
    return None


async def _load_plugin_lsp_servers(plugin: LoadedPlugin, errors: list[PluginError]) -> dict[str, Any] | None:
    """Stage-3 seam: `loadPluginLspServers` (`lspPluginIntegration.ts`). No plugin LSP server is loaded yet."""
    return None


async def _load_plugin_hooks(locations: SettingsLocations) -> None:
    """Stage-3 seam: `loadPluginHooks` (`loadPluginHooks.ts`), the full swap of the registered plugin hooks."""


def _reinitialize_lsp_server_manager() -> None:
    """Stage-3 seam: `reinitializeLspServerManager` (`services/lsp/manager.ts`); TDDAgents has no LSP manager."""


def _get_agent_definitions(locations: SettingsLocations) -> dict[str, AgentDefinition]:
    """`getAgentDefinitionsWithOverrides(getOriginalCwd())`; stage 3 adds the plugin agents to it."""
    return get_agent_definitions_with_overrides(project_dir=locations.project_root, user_home=locations.home)


# ── refreshActivePlugins ──────────────────────────────────────────────────────

async def refresh_active_plugins(
    set_app_state: SetAppState, locations: SettingsLocations
) -> RefreshActivePluginsResult:
    """
    `refreshActivePlugins`: clear every plugin cache (and the orphan exclusions), load all plugins
    afresh, then the commands and agents, fill each enabled plugin's MCP/LSP servers, swap them
    into `AppState.plugins` (consuming `needs_refresh`, bumping `mcp_plugin_reconnect_key`), and
    reload the plugin hooks. A hook-load failure counts as one more error, never a failure.
    """
    logger.debug("refreshActivePlugins: clearing all plugin caches")
    clear_all_caches()
    clear_plugin_cache_exclusions()

    plugin_result = await load_all_plugins(locations)
    plugin_commands = await _get_plugin_commands(locations)
    agent_definitions = _get_agent_definitions(locations)
    enabled, disabled, errors = plugin_result.enabled, plugin_result.disabled, plugin_result.errors

    async def mcp_one(plugin: LoadedPlugin) -> int:
        if plugin.mcp_servers:
            return len(plugin.mcp_servers)
        servers = await _load_plugin_mcp_servers(plugin, errors, locations)
        if servers:
            plugin.mcp_servers = servers
        return len(servers) if servers else 0

    async def lsp_one(plugin: LoadedPlugin) -> int:
        if plugin.lsp_servers:
            return len(plugin.lsp_servers)
        servers = await _load_plugin_lsp_servers(plugin, errors)
        if servers:
            plugin.lsp_servers = servers
        return len(servers) if servers else 0

    mcp_counts, lsp_counts = await asyncio.gather(
        asyncio.gather(*(mcp_one(p) for p in enabled)),
        asyncio.gather(*(lsp_one(p) for p in enabled)),
    )
    mcp_count, lsp_count = sum(mcp_counts), sum(lsp_counts)

    def swap(prev: AppState) -> AppState:
        plugins = replace(
            prev.plugins,
            enabled=tuple(enabled),
            disabled=tuple(disabled),
            commands=tuple(plugin_commands),
            errors=tuple(merge_plugin_errors(prev.plugins.errors, errors)),
            needs_refresh=False,
        )
        return replace(prev, plugins=plugins, mcp_plugin_reconnect_key=prev.mcp_plugin_reconnect_key + 1)

    set_app_state(swap)
    _reinitialize_lsp_server_manager()

    hook_load_failed = False
    try:
        await _load_plugin_hooks(locations)
    except Exception as exc:  # noqa: BLE001 - upstream counts a hook-load failure as an error, never a failure
        hook_load_failed = True
        logger.error("refreshActivePlugins: loadPluginHooks failed: %s", exc)

    hook_count = sum(_count_hooks(p.hooks_config) for p in enabled)
    logger.debug(
        "refreshActivePlugins: %d enabled, %d commands, %d agents, %d hooks, %d MCP, %d LSP",
        len(enabled), len(plugin_commands), len(agent_definitions), hook_count, mcp_count, lsp_count,
    )
    return RefreshActivePluginsResult(
        enabled_count=len(enabled),
        disabled_count=len(disabled),
        command_count=len(plugin_commands),
        agent_count=len(agent_definitions),
        hook_count=hook_count,
        mcp_count=mcp_count,
        lsp_count=lsp_count,
        error_count=len(errors) + (1 if hook_load_failed else 0),
        agent_definitions=agent_definitions,
        plugin_commands=plugin_commands,
    )


def _count_hooks(hooks_config: Mapping[str, Any] | None) -> int:
    """The hook commands of one plugin: every matcher's `hooks`, over every event."""
    if not hooks_config:
        return 0
    return sum(len(m.get("hooks") or []) for matchers in hooks_config.values() for m in (matchers or []))


def _error_key(error: Mapping[str, Any]) -> str:
    """`errorKey`."""
    if error["type"] == "generic-error":
        return f"generic-error:{error['source']}:{error['error']}"
    return f"{error['type']}:{error['source']}"


def merge_plugin_errors(
    existing: tuple[PluginError, ...] | list[PluginError], fresh: list[PluginError]
) -> list[PluginError]:
    """
    `mergePluginErrors`: keep the LSP-manager and plugin-component errors other systems recorded
    (deduplicated against the fresh ones), then the fresh load errors.
    """
    preserved = [e for e in existing if e["source"] == "lsp-manager" or e["source"].startswith("plugin:")]
    fresh_keys = {_error_key(e) for e in fresh}
    deduped = [e for e in preserved if _error_key(e) not in fresh_keys]
    return [*deduped, *fresh]


__all__ = ["RefreshActivePluginsResult", "SetAppState", "merge_plugin_errors", "refresh_active_plugins"]
