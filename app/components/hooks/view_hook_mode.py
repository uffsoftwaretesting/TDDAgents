"""
`/hooks`' read-only view of one hook.

Ported from `reference/claude-code/src/components/hooks/ViewHookMode.tsx` -> `ViewHookMode`,
`getContentFieldLabel`, `getContentFieldValue`: the event, the matcher (for an event with matchers),
the type, the source and the plugin, then the command, prompt or URL in a rounded box (the real
value, never the status message), the status message when set, and where to change it.
"""

from __future__ import annotations

from app.components.design_system.dialog import CANCELLED, DialogIO
from app.components.hooks.select_matcher_mode import ESC_TO_GO_BACK
from app.schemas.hooks import AgentHook, CommandHook, HookCommand, HttpHook, PromptHook
from app.utils.hooks.hooks_settings import IndividualHookConfig, hook_source_description_display_string

TO_MODIFY = "To modify or remove this hook, edit settings.json directly or ask TDDAgents to help."


def get_content_field_label(config: HookCommand) -> str:
    """`getContentFieldLabel`."""
    if isinstance(config, CommandHook):
        return "Command"
    if isinstance(config, HttpHook):
        return "URL"
    return "Prompt"


def get_content_field_value(config: HookCommand) -> str:
    """`getContentFieldValue`: the command, prompt or URL, bypassing the status message."""
    if isinstance(config, CommandHook):
        return config.command
    if isinstance(config, HttpHook):
        return config.url
    if isinstance(config, (PromptHook, AgentHook)):
        return config.prompt
    return config.type


def round_box(text: str) -> list[str]:
    """Ink's `borderStyle="round"` with one column of padding on each side."""
    lines = text.split("\n")
    width = max(len(line) for line in lines)
    return ["╭" + "─" * (width + 2) + "╮", *(f"│ {line:<{width}} │" for line in lines), "╰" + "─" * (width + 2) + "╯"]


def render_hook_details(hook: IndividualHookConfig, event_supports_matcher: bool) -> list[str]:
    """The view's lines."""
    lines = ["Hook details", "", f"Event: {hook.event}"]
    if event_supports_matcher:
        lines.append(f"Matcher: {hook.matcher or '(all)'}")
    lines += [f"Type: {hook.config.type}", f"Source: {hook_source_description_display_string(hook.source)}"]
    if hook.plugin_name:
        lines.append(f"Plugin: {hook.plugin_name}")
    lines += ["", f"{get_content_field_label(hook.config)}:", *round_box(get_content_field_value(hook.config))]
    status_message = getattr(hook.config, "status_message", None)
    if status_message:
        lines += ["", f"Status message: {status_message}"]
    return [*lines, "", TO_MODIFY]


async def view_hook(io: DialogIO, hook: IndividualHookConfig, event_supports_matcher: bool) -> None:
    """The view, until Esc (or any line) goes back."""
    await io.ask("\n".join([*render_hook_details(hook, event_supports_matcher), ESC_TO_GO_BACK]),
                 lambda _line: CANCELLED, ESC_TO_GO_BACK, CANCELLED)


__all__ = [
    "TO_MODIFY",
    "get_content_field_label",
    "get_content_field_value",
    "render_hook_details",
    "round_box",
    "view_hook",
]
