"""
`/hooks`' first view: every hook event, with how many hooks each has.

Ported from `reference/claude-code/src/components/hooks/SelectEventMode.tsx` -> `SelectEventMode`:
the "Hooks" dialog with its `N hooks configured` subtitle, the policy notice when only managed hooks
may run, the read-only notice, and one option per event (`Name (count)` when it has hooks, its
summary as the description). The read-only notice's "Learn more" link (Claude Code's
documentation) is left out: TDDAgents has no such page.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.components.design_system.dialog import Cancelled, DialogIO, select
from app.utils.hooks.hooks_config_manager import HookEventMetadata
from app.utils.string_utils import plural

#: `figures.info`.
INFO = "ℹ"

READ_ONLY_NOTICE = (f"{INFO} This menu is read-only. To add or modify hooks, edit settings.json directly or ask "
                    "TDDAgents.")
RESTRICTED_BY_POLICY = (
    f"{INFO} Hooks Restricted by Policy",
    "Only hooks from managed settings can run. User-defined hooks from ~/.tddagents/settings.json, "
    ".tddagents/settings.json, and .tddagents/settings.local.json are blocked.",
)


def event_option_label(name: str, count: int) -> str:
    """An event's label: its name, and its hook count when it has hooks."""
    return f"{name} ({count})" if count > 0 else name


async def select_event(
    io: DialogIO, hook_event_metadata: Mapping[str, HookEventMetadata], hooks_by_event: Mapping[str, int],
    total_hooks_count: int, restricted_by_policy: bool,
) -> str | Cancelled:
    """The events; the one picked, or `CANCELLED` on Esc."""
    lines = ["Hooks", f"{total_hooks_count} {plural(total_hooks_count, 'hook')} configured", ""]
    if restricted_by_policy:
        lines += [*RESTRICTED_BY_POLICY, ""]
    lines += [READ_ONLY_NOTICE, ""]
    names = list(hook_event_metadata)
    picked = await select(io, lines, [event_option_label(name, hooks_by_event.get(name, 0)) for name in names],
                          "cancel", [hook_event_metadata[name].summary for name in names])
    return picked if isinstance(picked, Cancelled) else names[picked]


__all__ = ["INFO", "READ_ONLY_NOTICE", "RESTRICTED_BY_POLICY", "event_option_label", "select_event"]
