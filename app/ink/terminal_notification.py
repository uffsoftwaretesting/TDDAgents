"""
The terminal's notifications: iTerm2, Kitty and Ghostty's own, and the bell.

Ported from `reference/claude-code/src/ink/useTerminalNotification.ts` -> `TerminalNotification`
(`notifyITerm2`, `notifyKitty`, `notifyGhostty`, `notifyBell`); the progress report (`OSC 9;4`) is not
ported. Upstream writes through Ink's `writeRaw`; here `write_raw` is the REPL terminal's raw output.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.ink.termio.osc import BEL, OSC_GHOSTTY, OSC_ITERM2, OSC_KITTY, osc, wrap_for_multiplexer


class TerminalNotification:
    """`useTerminalNotification()`'s methods, over a raw writer."""

    def __init__(self, write_raw: Callable[[str], None], *, terminal: str | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self._write_raw = write_raw
        self._terminal = terminal
        self._environ = environ

    def _send(self, sequence: str) -> None:
        self._write_raw(wrap_for_multiplexer(sequence, self._environ))

    def notify_iterm2(self, message: str, title: str | None = None) -> None:
        """`notifyITerm2`: OSC 9 with `title:\\nmessage`."""
        display = f"{title}:\n{message}" if title else message
        self._send(osc(OSC_ITERM2, f"\n\n{display}", terminal=self._terminal))

    def notify_kitty(self, message: str, title: str, notification_id: int) -> None:
        """`notifyKitty`: OSC 99's title, body, then focus."""
        self._send(osc(OSC_KITTY, f"i={notification_id}:d=0:p=title", title, terminal=self._terminal))
        self._send(osc(OSC_KITTY, f"i={notification_id}:p=body", message, terminal=self._terminal))
        self._send(osc(OSC_KITTY, f"i={notification_id}:d=1:a=focus", "", terminal=self._terminal))

    def notify_ghostty(self, message: str, title: str) -> None:
        """`notifyGhostty`: OSC 777 `notify`."""
        self._send(osc(OSC_GHOSTTY, "notify", title, message, terminal=self._terminal))

    def notify_bell(self) -> None:
        """`notifyBell`: a raw BEL, never wrapped (tmux's bell-action must see it)."""
        self._write_raw(BEL)


__all__ = ["TerminalNotification"]
