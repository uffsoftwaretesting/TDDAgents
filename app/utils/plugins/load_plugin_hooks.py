"""
The enabled plugins' hooks, registered on a run's hook dispatcher.

Ported from `reference/claude-code/src/utils/plugins/loadPluginHooks.ts` ->
`convertPluginHooksToMatchers`, `loadPluginHooks`, `clearPluginHookCache`,
`pruneRemovedPluginHooks`, `getPluginAffectingSettingsSnapshot`; and
`reference/claude-code/src/bootstrap/state.ts` -> `clearRegisteredPluginHooks` (here
`HookDispatcher.clear_registered_plugin_hooks`).

Upstream registers plugin hooks in the process-wide `STATE.registeredHooks`; here each run's
`HookDispatcher.registered_matchers` is that state (beside an SDK host's callbacks), so
`load_plugin_hooks(dispatcher)` swaps one run's plugin matchers: clear, then register, as one
step, so a plugin's Stop hook never goes missing between the two (gh-29767). Each matcher
carries its plugin's directory, name and id (`HookMatcher.plugin_root` / `plugin_name` /
`plugin_id`), which `execCommandHook` turns into `CLAUDE_PLUGIN_ROOT` and the rest.

The memo is a generation counter: a dispatcher that registered the current generation is
skipped, `clear_plugin_hook_cache` starts a new one (it never wipes the registered hooks, as
upstream). `prune_removed_plugin_hooks` drops, on every dispatcher that registered plugin
hooks, the hooks of plugins no longer enabled, without adding newly enabled ones (those wait
for `/reload-plugins`, as commands, agents and MCP servers do); `clear_all_plugin_caches` starts
it in the background.

Not ported: `setupPluginHookHotReload` and its settings snapshot listener (no settings change
detector here: settings are read from disk when a run opens), and bare mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from collections.abc import Mapping
from typing import Any, cast

from app.schemas.hooks import HookMatcher, parse_hooks_settings
from app.types.permissions import PermissionRuleSource
from app.types.plugin import LoadedPlugin
from app.utils.hooks.hook_events import HOOK_EVENTS
from app.utils.plugins.plugin_loader import load_all_plugins_cache_only
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_for_source,
    load_settings,
)

logger = logging.getLogger("TDDOrchestrator.Plugins")

_generation = 0
#: The dispatchers holding plugin hooks, by id (weakly: a finished run's dispatcher goes).
_dispatchers: weakref.WeakValueDictionary[int, Any] = weakref.WeakValueDictionary()
#: The fire-and-forget prunes (`pruneRemovedPluginHooks().catch(...)`), kept referenced.
_background: set[asyncio.Task[None]] = set()


def convert_plugin_hooks_to_matchers(plugin: LoadedPlugin) -> dict[str, list[HookMatcher]]:
    """`convertPluginHooksToMatchers`: every known event's matchers with hooks, tagged with the plugin."""
    matchers: dict[str, list[HookMatcher]] = {event: [] for event in HOOK_EVENTS}
    if not plugin.hooks_config:
        return matchers
    parsed = parse_hooks_settings(plugin.hooks_config, origin=f"plugin {plugin.name}")
    for event, event_matchers in parsed.items():
        if event not in matchers:
            continue
        for matcher in event_matchers:
            if matcher.hooks:
                matchers[event].append(HookMatcher(
                    hooks=matcher.hooks, matcher=matcher.matcher, plugin_root=plugin.path,
                    plugin_name=plugin.name, plugin_id=plugin.source))
    return matchers


def _locations_of(dispatcher: Any) -> SettingsLocations:
    locations = getattr(dispatcher, "settings_locations", None)
    if locations is None:
        raise ValueError("The hook dispatcher has no settings locations to load plugins from")
    return cast(SettingsLocations, locations)


async def load_plugin_hooks(dispatcher: Any) -> None:
    """
    `loadPluginHooks` (memoized per dispatcher until `clear_plugin_hook_cache`): the enabled
    plugins' hooks replace the dispatcher's plugin matchers, the SDK callbacks kept.
    """
    if dispatcher.plugin_hooks_generation == _generation:
        return
    generation = _generation
    result = await load_all_plugins_cache_only(_locations_of(dispatcher))
    all_hooks: dict[str, list[HookMatcher]] = {event: [] for event in HOOK_EVENTS}
    for plugin in result.enabled:
        if not plugin.hooks_config:
            continue
        logger.debug("Loading hooks from plugin: %s", plugin.name)
        for event, matchers in convert_plugin_hooks_to_matchers(plugin).items():
            all_hooks[event].extend(matchers)
    dispatcher.clear_registered_plugin_hooks()
    dispatcher.register_hook_callbacks({event: matchers for event, matchers in all_hooks.items() if matchers})
    dispatcher.plugin_hooks_generation = generation
    _dispatchers[id(dispatcher)] = dispatcher
    total = sum(len(m.hooks) for matchers in all_hooks.values() for m in matchers)
    logger.debug("Registered %d hooks from %d plugins", total, len(result.enabled))


def clear_plugin_hook_cache() -> None:
    """`clearPluginHookCache`: forget the memo only; the registered hooks stay until the next load."""
    global _generation
    _generation += 1


async def prune_removed_plugin_hooks() -> None:
    """
    `pruneRemovedPluginHooks`: on each dispatcher holding plugin hooks, keep only those whose
    plugin directory is still enabled. Nothing is added.
    """
    for dispatcher in list(_dispatchers.values()):
        if not any(m.plugin_root is not None for ms in dispatcher.registered_matchers.values() for m in ms):
            continue
        result = await load_all_plugins_cache_only(_locations_of(dispatcher))
        enabled_roots = {p.path for p in result.enabled}
        survivors = {
            event: [m for m in matchers if m.plugin_root is not None and m.plugin_root in enabled_roots]
            for event, matchers in dispatcher.registered_matchers.items()
        }
        dispatcher.clear_registered_plugin_hooks()
        dispatcher.register_hook_callbacks({event: kept for event, kept in survivors.items() if kept})


def prune_removed_plugin_hooks_in_background() -> None:
    """`pruneRemovedPluginHooks().catch(logError)`: started when an event loop runs, else skipped."""
    if not _dispatchers:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("pruneRemovedPluginHooks: no running event loop, skipped")
        return

    async def run() -> None:
        try:
            await prune_removed_plugin_hooks()
        except Exception as exc:  # noqa: BLE001 - upstream logs a failed prune
            logger.error("pruneRemovedPluginHooks failed: %s", exc)

    task = loop.create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)


def get_plugin_affecting_settings_snapshot(locations: SettingsLocations) -> str:
    """
    `getPluginAffectingSettingsSnapshot`: the four settings `load_all_plugins_cache_only` reads,
    key-sorted, as one comparable string.
    """
    merged = load_settings(locations)
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}

    def sort_keys(value: Any) -> dict[str, Any]:
        return dict(sorted(value.items())) if isinstance(value, Mapping) else {}

    return json.dumps({
        "enabledPlugins": sort_keys(merged.get("enabledPlugins")),
        "extraKnownMarketplaces": sort_keys(merged.get("extraKnownMarketplaces")),
        "strictKnownMarketplaces": policy.get("strictKnownMarketplaces") or [],
        "blockedMarketplaces": policy.get("blockedMarketplaces") or [],
    }, separators=(",", ":"))


__all__ = [
    "clear_plugin_hook_cache",
    "convert_plugin_hooks_to_matchers",
    "get_plugin_affecting_settings_snapshot",
    "load_plugin_hooks",
    "prune_removed_plugin_hooks",
    "prune_removed_plugin_hooks_in_background",
]
