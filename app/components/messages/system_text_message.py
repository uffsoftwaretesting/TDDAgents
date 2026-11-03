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
"""

from __future__ import annotations

from app.utils.messages import StopHookSummaryMessage

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


__all__ = ["BLACK_CIRCLE", "GUTTER", "render_stop_hook_summary"]
