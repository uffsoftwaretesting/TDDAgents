"""
The REPL's rendering of the stop-hook summary ("Ran N stop hooks").

Ported from `reference/claude-code/src/components/messages/SystemTextMessage.tsx` ->
`StopHookSummaryMessage`, as the external build renders it: the summary shows only when a hook
errored or prevented continuation (a summary with a `hookLabel`, the PreToolUse / PostToolUse timing
line upstream emits for its internal users only, shows as its child line). Its lines:

    ● Ran 2 stop hooks
      ⎿  Stop hook error: <the error>

With `verbose` (`--verbose`, the REPL's expanded view in place of upstream's ctrl+o transcript), each
hook's command (or `prompt: <text>`) is listed under it, then the stop reason when continuation was
prevented, then each error. The internal build's slow-hook timings are not shown here either.

`notice` is `SystemTextMessage`'s colouring of a line TDDAgents writes (user decision 2026-11-17), on a
terminal that shows colour: the text in the terminal's own foreground (`theme.TEXT`, crisp on a dark or a
light background, never dim), and only its mark in the level's colour — a warning's `⚠` in
`theme.warning` (an error's in `theme.error`), a leading `✔` in `theme.success`, plan mode's `⏸` in green.
The text is unchanged everywhere else.
"""

from __future__ import annotations

from typing import Literal

from rich.text import Text

from app.components.design_system import theme
from app.components.design_system.rich_render import to_ansi
from app.utils.messages import StopHookSummaryMessage

NoticeLevel = Literal["info", "success", "warning", "error", "plan"]
#: Each level's colour (`theme.ts`); `plan` is plan mode's.
NOTICE_STYLES: dict[str, str] = {"info": theme.INACTIVE, "success": theme.SUCCESS, "warning": theme.WARNING,
                                 "error": theme.ERROR, "plan": theme.PLAN_MODE}
#: `figures.warning`, the mark of a warning line.
WARNING_MARK = "⚠"

#: `BLACK_CIRCLE` and the `⎿` gutter.
BLACK_CIRCLE = "●"
GUTTER = "  ⎿  "


def _info_text(command: str, prompt_text: str | None) -> str:
    return f"prompt: {prompt_text or ''}" if command == "prompt" else command


def render_stop_hook_summary(message: StopHookSummaryMessage, *, verbose: bool = False) -> list[str]:
    """`StopHookSummaryMessage`: the lines it prints, none for a summary that is hidden."""
    count = message.hook_count
    hooks = "hook" if count == 1 else "hooks"
    if not message.hook_errors and not message.prevented_continuation and not message.hook_label:
        return []
    if message.hook_label:
        return [f"{GUTTER}Ran {count} {message.hook_label} {hooks}"]
    lines = [f"{BLACK_CIRCLE} Ran {count} stop {hooks}"]
    if verbose:
        lines += [f"{GUTTER}{_info_text(info.command, info.prompt_text)}" for info in message.hook_infos]
    if message.prevented_continuation and message.stop_reason:
        lines.append(f"{GUTTER}{message.stop_reason}")
    lines += [f"{GUTTER}Stop hook error: {error}" for error in message.hook_errors]
    return lines


#: The marks a notice may start with, coloured by its level.
NOTICE_MARKS = (WARNING_MARK, "✔", "⏸")


def notice(text: str, level: NoticeLevel, *, color: bool, width: int = 200) -> str:
    """
    `text` with `color`: crisp default text, its mark (a warning's `⚠`, added when missing; a leading `✔`
    or `⏸`) in the level's colour. Without `color`, `text` itself.
    """
    if not color:
        return text
    if level in ("warning", "error") and not text.startswith(WARNING_MARK):
        text = f"{WARNING_MARK} {text}"
    mark = next((m for m in NOTICE_MARKS if text.startswith(m + " ")), None)
    if mark is None or level == "info":
        return to_ansi(Text(text, style=theme.TEXT), width)
    return to_ansi(Text.assemble((mark, NOTICE_STYLES[level]), (text[len(mark):], theme.TEXT)), width)


__all__ = ["BLACK_CIRCLE", "GUTTER", "NOTICE_MARKS", "NOTICE_STYLES", "NoticeLevel", "notice",
           "render_stop_hook_summary"]
