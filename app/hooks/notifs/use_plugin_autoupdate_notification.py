"""
The notice that plugins were auto-updated, and how to apply the updates.

Ported from `reference/claude-code/src/hooks/notifs/usePluginAutoupdateNotification.tsx` ->
`usePluginAutoupdateNotification`: it registers with `onPluginsAutoUpdated` while the REPL is
mounted, and each batch of updated plugin ids becomes one notice, "Plugin(s) updated: <names> ·
Run /reload-plugins to apply" (each id's name before `@`; more than two are counted). The REPL has
no notification area (Ink's `addNotification`, a low-priority 10 s toast), so the notice is written
as a line, as the REPL's other notices are. Remote mode does not exist here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from app.utils.plugins.plugin_autoupdate import on_plugins_auto_updated

logger = logging.getLogger(__name__)


def plugin_display_name(plugin_id: str) -> str:
    """The plugin's name: the id up to its `@` (an id that starts with `@` is kept whole)."""
    at = plugin_id.find("@")
    return plugin_id[:at] if at > 0 else plugin_id


def plugin_autoupdate_notification_text(plugin_ids: Sequence[str]) -> str:
    """The notice for `plugin_ids` (non-empty)."""
    names = [plugin_display_name(plugin_id) for plugin_id in plugin_ids]
    shown = " and ".join(names) if len(names) <= 2 else f"{len(names)} plugins"
    return f"{'Plugin' if len(names) == 1 else 'Plugins'} updated: {shown} · Run /reload-plugins to apply"


def use_plugin_autoupdate_notification(write: Callable[[str], None]) -> Callable[[], None]:
    """Write each auto-update batch's notice with `write`; returns the unsubscribe function."""

    def notify(plugin_ids: Sequence[str]) -> None:
        logger.debug("Plugin autoupdate notification: %d plugin(s) updated", len(plugin_ids))
        if not plugin_ids:
            return
        write(plugin_autoupdate_notification_text(plugin_ids))

    return on_plugins_auto_updated(notify)


__all__ = ["plugin_autoupdate_notification_text", "plugin_display_name", "use_plugin_autoupdate_notification"]
