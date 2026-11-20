"""
Notifying the user that TDDAgents needs them: the Notification hooks, then the terminal.

Ported from `reference/claude-code/src/services/notifier.ts` -> `sendNotification`, `sendToChannel`,
`sendAuto`, `generateKittyId`, `DEFAULT_TITLE` (TDDAgents for Claude Code). The channel is the global
config's `preferredNotifChannel` (`utils/config.ts`, default `auto`).

Divergence (user decision 2026-11-20): upstream's `auto` notifies only in iTerm2, Kitty, Ghostty and (with
its bell setting) Apple Terminal, and does nothing anywhere else; here `auto` rings the terminal bell
there instead (WSL and Windows Terminal, gnome-terminal, VS Code…), so a finished or waiting session is
always announced. `notifications_disabled` still turns every channel off. Apple Terminal's bell-setting
probe (`osascript`, `defaults`) is not ported: it rings the bell.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any, Protocol

from app.utils.env import detect_terminal

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "TDDAgents"

#: `NotificationChannel` (`utils/configConstants.ts`).
NOTIFICATION_CHANNELS = ("auto", "iterm2", "iterm2_with_bell", "terminal_bell", "kitty", "ghostty",
                         "notifications_disabled")


@dataclass(frozen=True, slots=True)
class NotificationOptions:
    """`NotificationOptions`."""

    message: str
    notification_type: str
    title: str | None = None


class TerminalNotifier(Protocol):
    """`TerminalNotification` (`ink/useTerminalNotification.ts`), as `app/ink/terminal_notification.py`."""

    def notify_iterm2(self, message: str, title: str | None = None) -> None: ...

    def notify_kitty(self, message: str, title: str, notification_id: int) -> None: ...

    def notify_ghostty(self, message: str, title: str) -> None: ...

    def notify_bell(self) -> None: ...


def generate_kitty_id() -> int:
    """`generateKittyId`."""
    return random.randrange(10000)  # noqa: S311 - an id for the terminal, not a secret


def send_auto(opts: NotificationOptions, terminal: TerminalNotifier, terminal_name: str | None) -> str:
    """`sendAuto`, with the bell where upstream has no method (see the module's divergence)."""
    title = opts.title or DEFAULT_TITLE
    if terminal_name == "iTerm.app":
        terminal.notify_iterm2(opts.message, opts.title)
        return "iterm2"
    if terminal_name == "kitty":
        terminal.notify_kitty(opts.message, title, generate_kitty_id())
        return "kitty"
    if terminal_name == "ghostty":
        terminal.notify_ghostty(opts.message, title)
        return "ghostty"
    terminal.notify_bell()
    return "terminal_bell"


def send_to_channel(channel: str, opts: NotificationOptions, terminal: TerminalNotifier,
                    terminal_name: str | None = None) -> str:
    """`sendToChannel`: the method used, `none` for an unknown channel, `error` when writing failed."""
    title = opts.title or DEFAULT_TITLE
    try:
        if channel == "auto":
            return send_auto(opts, terminal, terminal_name)
        if channel == "iterm2":
            terminal.notify_iterm2(opts.message, opts.title)
            return "iterm2"
        if channel == "iterm2_with_bell":
            terminal.notify_iterm2(opts.message, opts.title)
            terminal.notify_bell()
            return "iterm2_with_bell"
        if channel == "kitty":
            terminal.notify_kitty(opts.message, title, generate_kitty_id())
            return "kitty"
        if channel == "ghostty":
            terminal.notify_ghostty(opts.message, title)
            return "ghostty"
        if channel == "terminal_bell":
            terminal.notify_bell()
            return "terminal_bell"
        if channel == "notifications_disabled":
            return "disabled"
        return "none"
    except Exception as error:  # noqa: BLE001 - upstream reports 'error' and moves on
        logger.debug("notification failed: %s", error)
        return "error"


async def send_notification(opts: NotificationOptions, terminal: TerminalNotifier, *, dispatcher: Any = None,
                            channel: str | None = None, terminal_name: str | None = None) -> str:
    """
    `sendNotification`: the Notification hooks first (`executeNotificationHooks`), then the configured
    channel. Returns the method used (upstream logs it as `tengu_notification_method_used`).
    """
    from app.utils.config import get_preferred_notif_channel
    from app.utils.hooks.hooks import execute_notification_hooks

    try:
        await execute_notification_hooks(dispatcher, opts.message, opts.notification_type, opts.title)
    except Exception as error:  # noqa: BLE001 - a failing hook never keeps the user from being told
        logger.debug("notification hooks failed: %s", error)
    chosen = channel if channel is not None else get_preferred_notif_channel()
    name = terminal_name if terminal_name is not None else detect_terminal()
    return send_to_channel(chosen, opts, terminal, name)


__all__ = ["DEFAULT_TITLE", "NOTIFICATION_CHANNELS", "NotificationOptions", "TerminalNotifier",
           "generate_kitty_id", "send_auto", "send_notification", "send_to_channel"]
