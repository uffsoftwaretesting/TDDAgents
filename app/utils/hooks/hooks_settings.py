"""
The configured hooks as `/hooks` lists them: each hook with its event, matcher and source.

Ported from `reference/claude-code/src/utils/hooks/hooksSettings.ts` -> `HookSource`,
`IndividualHookConfig`, `getHookDisplayText` (here `hook_display_text`, `app/utils/hooks/hooks.py`),
`getAllHooks`, `getHooksForEvent`, `hookSourceDescriptionDisplayString`,
`hookSourceHeaderDisplayString`, `hookSourceInlineDisplayString`, `sortMatchersByPriority`, and
`src/utils/settings/constants.ts` -> `SOURCES` (the editable sources in display order).

`get_all_hooks` reads the three editable settings files (user, project, local; a file two sources
resolve to is read once) as the settings readers do, from disk on every call, so the list is the
files' current content; policy and flag settings are not listed (upstream lists neither). With
policy's `allowManagedHooksOnly` no settings hook is listed. The session's hooks follow: the run's
session-scoped matchers (`HookDispatcher.session_matchers`, upstream's `AppState.sessionHooks` for
the session id), without the function hooks (`getSessionHooks` -> `convertToHookMatchers` leaves
them out).

The display strings carry the product substitutions: `~/.tddagents/`, `.tddagents/`, and "TDDAgents"
for "Claude Code".
"""

from __future__ import annotations

import functools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.schemas.hooks import CallbackHook, FunctionHook, HookCommand, HookMatcher, parse_hooks_settings
from app.types.permissions import PermissionRuleSource
from app.utils.settings.settings import (
    SETTINGS_SOURCES,
    SettingsLocations,
    get_settings_for_source,
    settings_file_path_for_source,
)

#: `HookSource`'s values: the editable settings sources, policy, then the non-settings sources.
PLUGIN_HOOK = "pluginHook"
SESSION_HOOK = "sessionHook"
BUILTIN_HOOK = "builtinHook"

#: `SOURCES`: the editable settings sources in display order (lower index = higher priority).
SOURCES: tuple[str, ...] = (
    PermissionRuleSource.LOCAL_SETTINGS.value,
    PermissionRuleSource.PROJECT_SETTINGS.value,
    PermissionRuleSource.USER_SETTINGS.value,
)


@dataclass(frozen=True, slots=True)
class IndividualHookConfig:
    """`IndividualHookConfig`: one hook, the event and matcher it is configured under, and its source."""

    event: str
    config: HookCommand
    source: str
    matcher: str | None = None
    plugin_name: str | None = None


def _listed(hook: HookCommand) -> bool:
    """A hook the settings format can hold (`convertToHookMatchers` drops function hooks; callbacks are code)."""
    return not isinstance(hook, (FunctionHook, CallbackHook))


def get_all_hooks(
    locations: SettingsLocations, session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[IndividualHookConfig]:
    """`getAllHooks`: the editable settings files' hooks, then the session's."""
    hooks: list[IndividualHookConfig] = []
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    restricted_to_managed_only = (policy or {}).get("allowManagedHooksOnly") is True
    if not restricted_to_managed_only:
        seen_files: set[str] = set()
        for source in SETTINGS_SOURCES:
            path = settings_file_path_for_source(source, locations)
            if path is not None:
                resolved = str(path.resolve())
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
            source_settings = get_settings_for_source(source, locations)
            if not source_settings or not source_settings.get("hooks"):
                continue
            for event, matchers in parse_hooks_settings(source_settings["hooks"], origin=str(path)).items():
                for matcher in matchers:
                    hooks.extend(IndividualHookConfig(event=event, config=hook, matcher=matcher.matcher,
                                                      source=source.value)
                                 for hook in matcher.hooks)
    for event, session in (session_matchers or {}).items():
        for matcher in session:
            hooks.extend(IndividualHookConfig(event=event, config=hook, matcher=matcher.matcher, source=SESSION_HOOK)
                         for hook in matcher.hooks if _listed(hook))
    return hooks


def get_hooks_for_event(
    locations: SettingsLocations, event: str, session_matchers: Mapping[str, Sequence[HookMatcher]] | None = None,
) -> list[IndividualHookConfig]:
    """`getHooksForEvent`."""
    return [hook for hook in get_all_hooks(locations, session_matchers) if hook.event == event]


def hook_source_description_display_string(source: str) -> str:
    """`hookSourceDescriptionDisplayString`."""
    return {
        PermissionRuleSource.USER_SETTINGS.value: "User settings (~/.tddagents/settings.json)",
        PermissionRuleSource.PROJECT_SETTINGS.value: "Project settings (.tddagents/settings.json)",
        PermissionRuleSource.LOCAL_SETTINGS.value: "Local settings (.tddagents/settings.local.json)",
        PLUGIN_HOOK: "Plugin hooks (~/.tddagents/plugins/*/hooks/hooks.json)",
        SESSION_HOOK: "Session hooks (in-memory, temporary)",
        BUILTIN_HOOK: "Built-in hooks (registered internally by TDDAgents)",
    }.get(source, source)


def hook_source_header_display_string(source: str) -> str:
    """`hookSourceHeaderDisplayString`."""
    return {
        PermissionRuleSource.USER_SETTINGS.value: "User Settings",
        PermissionRuleSource.PROJECT_SETTINGS.value: "Project Settings",
        PermissionRuleSource.LOCAL_SETTINGS.value: "Local Settings",
        PLUGIN_HOOK: "Plugin Hooks",
        SESSION_HOOK: "Session Hooks",
        BUILTIN_HOOK: "Built-in Hooks",
    }.get(source, source)


def hook_source_inline_display_string(source: str) -> str:
    """`hookSourceInlineDisplayString`."""
    return {
        PermissionRuleSource.USER_SETTINGS.value: "User",
        PermissionRuleSource.PROJECT_SETTINGS.value: "Project",
        PermissionRuleSource.LOCAL_SETTINGS.value: "Local",
        PLUGIN_HOOK: "Plugin",
        SESSION_HOOK: "Session",
        BUILTIN_HOOK: "Built-in",
    }.get(source, source)


#: The priority of a source without one in `SOURCES` (`sourcePriority[source]` is `undefined`, so
#: `Math.min` gives `NaN`, and a comparator returning `NaN` keeps the pair's order).
_NO_PRIORITY = math.nan


def _source_priority(source: str) -> float:
    if source in (PLUGIN_HOOK, BUILTIN_HOOK):
        return 999
    return float(SOURCES.index(source)) if source in SOURCES else _NO_PRIORITY


def _highest_priority(hooks: Sequence[IndividualHookConfig]) -> float:
    priorities = [_source_priority(source) for source in dict.fromkeys(h.source for h in hooks)]
    if not priorities:
        return math.inf  # `Math.min()` of nothing
    return math.nan if any(math.isnan(p) for p in priorities) else min(priorities)


def _locale_key(text: str) -> tuple[str, str]:
    """`String.prototype.localeCompare`'s root order, near enough: case-insensitive, lower case first."""
    return text.casefold(), text.swapcase()


def sort_matchers_by_priority(
    matchers: Sequence[str], hooks_by_event_and_matcher: Mapping[str, Mapping[str, Sequence[IndividualHookConfig]]],
    selected_event: str,
) -> list[str]:
    """`sortMatchersByPriority`: by the matcher's highest-priority source, then by name."""
    by_matcher = hooks_by_event_and_matcher.get(selected_event) or {}

    def compare(a: str, b: str) -> int:
        a_priority = _highest_priority(by_matcher.get(a) or ())
        b_priority = _highest_priority(by_matcher.get(b) or ())
        if math.isnan(a_priority) or math.isnan(b_priority):
            return 0
        if a_priority != b_priority:
            return -1 if a_priority < b_priority else 1
        a_key, b_key = _locale_key(a), _locale_key(b)
        return -1 if a_key < b_key else (1 if a_key > b_key else 0)

    return sorted(matchers, key=functools.cmp_to_key(compare))


__all__ = [
    "BUILTIN_HOOK",
    "IndividualHookConfig",
    "PLUGIN_HOOK",
    "SESSION_HOOK",
    "SOURCES",
    "get_all_hooks",
    "get_hooks_for_event",
    "hook_source_description_display_string",
    "hook_source_header_display_string",
    "hook_source_inline_display_string",
    "sort_matchers_by_priority",
]
