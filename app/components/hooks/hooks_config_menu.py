"""
`/hooks`' dialog: a read-only browser of the configured hooks.

Ported from `reference/claude-code/src/components/hooks/HooksConfigMenu.tsx` -> `HooksConfigMenu`:
its view states (`select-event` -> `select-matcher` -> `select-hook` -> `view-hook`, an event without
matchers going straight to its hooks), Esc going back one view (closing the dialog from the first
with `Hooks dialog dismissed`), the per-event and total counts, and the "Hook Configuration -
Disabled" screen shown instead when settings set `disableAllHooks` (saying whether a managed
settings file did it). The menu edits nothing: it points at settings.json or at asking TDDAgents.

The hooks are read when the dialog opens (`groupHooksByEventAndMatcher`, from the settings files as
they are now, the run's session hooks and its plugins' hooks); `HooksMenuContext` carries what it
reads them from. Upstream re-reads the policy flags when policy settings change while the dialog is
open (`useSettingsChange`); here every view reads the settings files when it is drawn, so there is
nothing to subscribe to.

*Divergence:* the disabled screen's "· StatusLine will not be displayed" line is left out (this
build has no status line).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.components.design_system.dialog import CANCELLED, Cancelled, DialogIO
from app.components.hooks.select_event_mode import select_event
from app.components.hooks.select_hook_mode import select_hook
from app.components.hooks.select_matcher_mode import select_matcher
from app.components.hooks.view_hook_mode import view_hook
from app.schemas.hooks import HookMatcher
from app.types.permissions import PermissionRuleSource
from app.utils.hooks.hooks_config_manager import (
    HooksByEventAndMatcher,
    get_hook_event_metadata,
    get_hooks_for_matcher,
    get_matcher_metadata,
    get_sorted_matchers_for_event,
    group_hooks_by_event_and_matcher,
)
from app.utils.settings.settings import SettingsLocations, get_initial_settings, get_settings_for_source
from app.utils.string_utils import plural

DISMISSED = "Hooks dialog dismissed"
ESC_TO_CLOSE = "Esc to close"


@dataclass(frozen=True, slots=True)
class HooksMenuContext:
    """What the menu reads: the run's settings files, its tool names, and its session and plugin hooks."""

    locations: SettingsLocations
    tool_names: tuple[str, ...] = ()
    session_matchers: Mapping[str, Sequence[HookMatcher]] = field(default_factory=dict)
    registered_matchers: Mapping[str, Sequence[HookMatcher]] = field(default_factory=dict)


def _policy(context: HooksMenuContext) -> dict[str, object]:
    return get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, context.locations) or {}


def hooks_disabled(context: HooksMenuContext) -> bool:
    """`settings?.disableAllHooks === true`, over the merged settings."""
    return get_initial_settings(context.locations).get("disableAllHooks") is True


def disabled_by_policy(context: HooksMenuContext) -> bool:
    """Disabled, and by the managed settings."""
    return hooks_disabled(context) and _policy(context).get("disableAllHooks") is True


def restricted_by_policy(context: HooksMenuContext) -> bool:
    """Policy's `allowManagedHooksOnly`."""
    return _policy(context).get("allowManagedHooksOnly") is True


def hooks_by_event(grouped: HooksByEventAndMatcher) -> tuple[dict[str, int], int]:
    """The hook count of each event, and the total."""
    counts = {event: sum(len(hooks) for hooks in matchers.values()) for event, matchers in grouped.items()}
    return counts, sum(counts.values())


def render_disabled(total_hooks_count: int, by_policy: bool) -> list[str]:
    """The "Hook Configuration - Disabled" screen."""
    lines = [
        "Hook Configuration - Disabled", "",
        f"All hooks are currently disabled{' by a managed settings file' if by_policy else ''}. You have "
        f"{total_hooks_count} configured {plural(total_hooks_count, 'hook')} that "
        f"{plural(total_hooks_count, 'is', 'are')} not running.",
        "", "When hooks are disabled:", "· No hook commands will execute",
        "· Tool operations will proceed without hook validation",
    ]
    if not by_policy:
        lines += ["", 'To re-enable hooks, remove "disableAllHooks" from settings.json or ask TDDAgents.']
    return lines


async def run_hooks_config_menu(io: DialogIO, context: HooksMenuContext) -> str:
    """`HooksConfigMenu`: the dialog, until Esc closes it; its result is what the REPL shows."""
    tool_names = context.tool_names
    metadata = get_hook_event_metadata(tool_names)
    grouped = group_hooks_by_event_and_matcher(
        context.locations, tool_names, context.session_matchers, context.registered_matchers)
    counts, total = hooks_by_event(grouped)
    if hooks_disabled(context):
        await io.ask("\n".join([*render_disabled(total, disabled_by_policy(context)), ESC_TO_CLOSE]),
                     lambda _line: None, ESC_TO_CLOSE, CANCELLED)
        return DISMISSED
    while True:
        event = await select_event(io, metadata, counts, total, restricted_by_policy(context))
        if isinstance(event, Cancelled):
            return DISMISSED
        has_matchers = get_matcher_metadata(event, tool_names) is not None
        while True:
            matcher = ""
            if has_matchers:
                picked = await select_matcher(io, event, get_sorted_matchers_for_event(grouped, event), grouped,
                                              metadata[event].description)
                if isinstance(picked, Cancelled):
                    break
                matcher = picked
            if await _browse_hooks(io, context, grouped, event, matcher, has_matchers):
                break


async def _browse_hooks(
    io: DialogIO, context: HooksMenuContext, grouped: HooksByEventAndMatcher, event: str, matcher: str,
    has_matchers: bool,
) -> bool:
    """`select-hook` and `view-hook`; whether Esc leaves for the events (an event without matchers)."""
    metadata = get_hook_event_metadata(context.tool_names)[event]
    while True:
        hook = await select_hook(io, event, matcher, get_hooks_for_matcher(grouped, event, matcher), metadata)
        if isinstance(hook, Cancelled):
            return not has_matchers
        await view_hook(io, hook, has_matchers)


__all__ = [
    "DISMISSED",
    "ESC_TO_CLOSE",
    "HooksMenuContext",
    "disabled_by_policy",
    "hooks_by_event",
    "hooks_disabled",
    "render_disabled",
    "restricted_by_policy",
    "run_hooks_config_menu",
]
