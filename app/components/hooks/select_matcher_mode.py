"""
`/hooks`' view of one event's matchers.

Ported from `reference/claude-code/src/components/hooks/SelectMatcherMode.tsx` -> `SelectMatcherMode`:
the `<event> - Matchers` dialog, its description as the subtitle, and one option per matcher (in
priority order), labelled `[<sources>] <matcher>` (`(all)` for the empty matcher) with its hook
count; an event without hooks says so and how to add one.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.components.design_system.dialog import CANCELLED, Cancelled, DialogIO, select
from app.utils.hooks.hooks_config_manager import HooksByEventAndMatcher
from app.utils.hooks.hooks_settings import hook_source_inline_display_string
from app.utils.string_utils import plural

NO_HOOKS = "No hooks configured for this event."
HOW_TO_ADD = "To add hooks, edit settings.json directly or ask TDDAgents."
ESC_TO_GO_BACK = "Esc to go back"


async def show_no_hooks(io: DialogIO, title: str, subtitle: str) -> Cancelled:
    """An event (or matcher) with nothing configured, until Esc (or any line) goes back."""
    await io.ask("\n".join([title, subtitle, "", NO_HOOKS, HOW_TO_ADD, ESC_TO_GO_BACK]), lambda _line: CANCELLED,
                 ESC_TO_GO_BACK, CANCELLED)
    return CANCELLED


def matcher_option(event: str, matcher: str, hooks_by_event_and_matcher: HooksByEventAndMatcher) -> tuple[str, str]:
    """A matcher's label (`[<sources>] <matcher>`) and description (its hook count)."""
    hooks = (hooks_by_event_and_matcher.get(event) or {}).get(matcher) or []
    sources = ", ".join(hook_source_inline_display_string(s) for s in dict.fromkeys(h.source for h in hooks))
    return f"[{sources}] {matcher or '(all)'}", f"{len(hooks)} {plural(len(hooks), 'hook')}"


async def select_matcher(
    io: DialogIO, selected_event: str, matchers_for_selected_event: Sequence[str],
    hooks_by_event_and_matcher: HooksByEventAndMatcher, event_description: str,
) -> str | Cancelled:
    """The event's matchers; the one picked, or `CANCELLED` on Esc."""
    title = f"{selected_event} - Matchers"
    if not matchers_for_selected_event:
        return await show_no_hooks(io, title, event_description)
    options = [matcher_option(selected_event, m, hooks_by_event_and_matcher) for m in matchers_for_selected_event]
    picked = await select(io, [title, event_description, ""], [label for label, _ in options], "go back",
                          [description for _, description in options])
    return picked if isinstance(picked, Cancelled) else matchers_for_selected_event[picked]


__all__ = ["ESC_TO_GO_BACK", "HOW_TO_ADD", "NO_HOOKS", "matcher_option", "select_matcher", "show_no_hooks"]
