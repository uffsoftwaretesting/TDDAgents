"""
The workspace trust check, asked once per project folder before an interactive session opens.

Ported from `reference/claude-code/src/components/TrustDialog/TrustDialog.tsx` -> `TrustDialog`
(its text, its two options, `onChange`: accept saves `hasTrustDialogAccepted` in the project's
config entry, or, in the home directory, trusts this process only; refuse exits 1, Esc exits 0)
and `src/interactiveHelpers.tsx` -> `showSetupScreens`' trust step (skipped when the folder is
already trusted; afterwards the session's trust is set, `setSessionTrustAccepted(true)`). "Claude
Code" is "TDDAgents". Upstream shows it before the REPL mounts, so no project hook, MCP server,
plugin or setting runs before the user agrees; `app/session/driver.py` calls it before the session
opens. A headless (`-p`) session never shows it: trust is implicit there, as upstream's print
mode (`main.tsx` -> `prefetchSystemContextIfSafe`, `utils/hooks.ts` -> `shouldSkipHookDueToTrust`).

Not ported: `TrustDialog/utils.ts` and the `commands` prop, which only feed the dialog's analytics
events (`tengu_trust_dialog_shown` / `_accept`); the "Security guide" link, which points at Claude
Code's documentation; and Ctrl-C / Ctrl-D pressed twice (`useExitOnCtrlCDWithKeybindings`): closing
the input (Ctrl-D) exits 1 at once.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.bootstrap.state import set_session_trust_accepted
from app.components.design_system.dialog import choice_hint, parse_choice, render_options
from app.utils.config import HAS_TRUST_DIALOG_ACCEPTED, check_has_trust_dialog_accepted, save_current_project_config

TITLE = "Accessing workspace:"
SAFETY_CHECK = (
    "Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known open "
    "source project, or work from your team). If not, take a moment to review what's in this folder first.")
ABILITIES = "TDDAgents'll be able to read, edit, and execute files here."
OPTIONS = ("Yes, I trust this folder", "No, exit")
PROMPT = "❯ "
#: `_next_answer` when Esc was pressed.
ESCAPE = object()


class TrustDialogExit(Exception):
    """The user refused (exit code 1) or pressed Esc (0): the process ends without a session."""

    def __init__(self, exit_code: int) -> None:
        super().__init__(f"trust dialog exited with {exit_code}")
        self.exit_code = exit_code


def render_trust_dialog(cwd: str | Path) -> str:
    """The dialog: its title, the folder, why it asks, and the options."""
    return "\n".join([TITLE, "", str(cwd), "", SAFETY_CHECK, ABILITIES, "", *render_options(OPTIONS),
                      choice_hint(len(OPTIONS), "cancel")])


def accept_trust(cwd: str | Path, home: str | Path | None = None) -> None:
    """`onChange('enable_all')`: the home directory is trusted for this process, any other folder for good."""
    if Path(home if home is not None else Path.home()).resolve() == Path(cwd).resolve():
        set_session_trust_accepted(True)
        return
    save_current_project_config(cwd, lambda current: {**current, HAS_TRUST_DIALOG_ACCEPTED: True})


async def _next_answer(io: Any, escape: asyncio.Event) -> object:
    """The next line, `None` on Ctrl-D, or `ESCAPE` when Esc was pressed."""
    reading = asyncio.ensure_future(io.read_line(PROMPT))
    pressed = asyncio.ensure_future(escape.wait())
    await asyncio.wait({reading, pressed}, return_when=asyncio.FIRST_COMPLETED)
    if not reading.done():
        reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        escape.clear()
        return ESCAPE
    pressed.cancel()
    line: str | None = reading.result()
    return line


async def show_trust_dialog(
    io: Any, cwd: str | Path, escape: asyncio.Event, *, home: str | Path | None = None
) -> None:
    """
    `showSetupScreens`' trust step: nothing when `cwd` is trusted; otherwise the dialog, until the
    user accepts (saved) or leaves (`TrustDialogExit`). The session's trust is set either way it passes.
    `escape` is set by the terminal's Esc key while the dialog waits.
    """
    if not check_has_trust_dialog_accepted(cwd):
        io.write(render_trust_dialog(cwd))
        while True:
            line = await _next_answer(io, escape)
            if line is None:
                raise TrustDialogExit(1)
            if line is ESCAPE:
                raise TrustDialogExit(0)
            choice = parse_choice(len(OPTIONS), str(line))
            if choice == 1:
                raise TrustDialogExit(1)
            if choice == 0:
                accept_trust(cwd, home)
                break
            io.write(f"Choose 1-{len(OPTIONS)}.")
    set_session_trust_accepted(True)


__all__ = [
    "ABILITIES",
    "OPTIONS",
    "SAFETY_CHECK",
    "TITLE",
    "TrustDialogExit",
    "accept_trust",
    "render_trust_dialog",
    "show_trust_dialog",
]
