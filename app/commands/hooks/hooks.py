"""
The `/hooks` command: the hooks menu.

Ported from `reference/claude-code/src/commands/hooks/hooks.tsx` -> `call` (the tool names of the
session's tool pool, MCP tools included, handed to the menu) and `commands/hooks/index.ts` (the
command's description; `immediate`, so it opens mid-turn; registered in `app/commands/commands.py`).
The dialog is `app/components/hooks/hooks_config_menu.py`; its result is what the REPL shows.

**Opening it reloads the hooks** (`updateHooksConfigSnapshot`): the run's hooks snapshot is recaptured
from the settings files first, so a hook written where the settings watcher does not look (a
`.tddagents/` that had no settings file when the session started) runs from the next event on. This
is what upstream's `update-config` skill tells the user `/hooks` does ("open `/hooks` once (reloads
config)") and what `updateHooksConfigSnapshot`'s own comment describes ("the user edits
settings.json externally and then runs /hooks"). *Divergence:* v2.1.88's `HooksConfigMenu` itself
no longer calls it; the reload keeps the skill's instruction true.
"""

from __future__ import annotations

from typing import Any

from app.components.design_system.dialog import DialogIO
from app.components.hooks.hooks_config_menu import HooksMenuContext, run_hooks_config_menu
from app.utils.debug import log_for_debugging
from app.utils.hooks.hooks_config_snapshot import update_hooks_config_snapshot

#: `commands/hooks/index.ts`' description.
DESCRIPTION = "View hook configurations for tool events"


def menu_context(locations: Any, tools: Any, dispatcher: Any) -> HooksMenuContext:
    """What the menu reads: the run's settings, its tools' names, and its dispatcher's session and plugin hooks."""
    return HooksMenuContext(
        locations=locations,
        tool_names=tuple(str(tool.name) for tool in tools or ()),
        session_matchers=dict(getattr(dispatcher, "session_matchers", None) or {}),
        registered_matchers=dict(getattr(dispatcher, "registered_matchers", None) or {}),
    )


async def call(io: DialogIO, context: HooksMenuContext, dispatcher: Any = None) -> str:
    """`call(onDone, context)`: the hooks reloaded into the run, then the menu; its result."""
    if dispatcher is not None:
        update_hooks_config_snapshot(dispatcher)
        log_for_debugging("Hooks config snapshot updated from /hooks")
    return await run_hooks_config_menu(io, context)


__all__ = ["DESCRIPTION", "call", "menu_context"]
