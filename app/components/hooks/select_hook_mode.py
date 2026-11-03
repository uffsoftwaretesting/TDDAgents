"""
`/hooks`' view of the hooks under one event and matcher.

Ported from `reference/claude-code/src/components/hooks/SelectHookMode.tsx` -> `SelectHookMode`: the
dialog titled `<event> - Matcher: <matcher>` (the event alone when it has no matchers), the event's
description as the subtitle, and one option per hook, labelled `[<type>] <display text>` with its
source (and plugin) as the description.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.components.design_system.dialog import Cancelled, DialogIO, select
from app.components.hooks.select_matcher_mode import show_no_hooks
from app.utils.hooks.hooks import hook_display_text
from app.utils.hooks.hooks_config_manager import HookEventMetadata
from app.utils.hooks.hooks_settings import PLUGIN_HOOK, IndividualHookConfig, hook_source_header_display_string


def hook_list_title(selected_event: str, selected_matcher: str | None, metadata: HookEventMetadata) -> str:
    """The dialog's title."""
    if metadata.matcher_metadata is not None:
        return f"{selected_event} - Matcher: {selected_matcher or '(all)'}"
    return selected_event


def hook_option(hook: IndividualHookConfig) -> tuple[str, str]:
    """A hook's label and description."""
    source = hook_source_header_display_string(hook.source)
    if hook.source == PLUGIN_HOOK and hook.plugin_name:
        source = f"{source} ({hook.plugin_name})"
    return f"[{hook.config.type}] {hook_display_text(hook.config)}", source


async def select_hook(
    io: DialogIO, selected_event: str, selected_matcher: str | None,
    hooks_for_selected_matcher: Sequence[IndividualHookConfig], hook_event_metadata: HookEventMetadata,
) -> IndividualHookConfig | Cancelled:
    """The hooks; the one picked, or `CANCELLED` on Esc."""
    title = hook_list_title(selected_event, selected_matcher, hook_event_metadata)
    if not hooks_for_selected_matcher:
        return await show_no_hooks(io, title, hook_event_metadata.description)
    options = [hook_option(hook) for hook in hooks_for_selected_matcher]
    picked = await select(io, [title, hook_event_metadata.description, ""], [label for label, _ in options],
                          "go back", [description for _, description in options])
    return picked if isinstance(picked, Cancelled) else hooks_for_selected_matcher[picked]


__all__ = ["hook_list_title", "hook_option", "select_hook"]
