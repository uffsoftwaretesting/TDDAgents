"""
The setup screens an interactive session shows before its REPL opens, after the trust dialog.

Ported from `reference/claude-code/src/interactiveHelpers.tsx` -> `showSetupDialog` (here
`SetupDialogIO`: a dialog asked on the terminal before the REPL exists, Esc read from the
terminal's interrupt) and `showSetupScreens`' MCP step: "If settings are valid, check for any
mcp.json servers that need approval" (`getSettingsWithAllErrors`, then
`handleMcpjsonServerApprovals`, `app/services/mcp_server_approval.py`); then `main.tsx`' settings
validation step (`launch_invalid_settings_dialog`, `dialogLaunchers.tsx` ->
`launchInvalidSettingsDialog`). The trust step itself is `app/components/trust_dialog/trust_dialog.py`;
`app/session/driver.py` runs the three, in that order, before the session (and its MCP connections)
opens.

Upstream's `Dialog` exits the process on Ctrl-C / Ctrl-D pressed twice
(`useExitOnCtrlCDWithKeybindings`, Ink's `exit`); here closing the input (Ctrl-D) exits 0 at once
(`SetupDialogExit`), as the trust dialog exits on it at once. Not ported: the onboarding, the
CLAUDE.md external-includes, Grove, bypass-permissions and other setup dialogs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.components.invalid_settings_dialog import invalid_settings_dialog
from app.components.trust_dialog.trust_dialog import TrustDialogExit
from app.services.mcp_server_approval import handle_mcpjson_server_approvals
from app.utils.settings.all_errors import get_settings_with_all_errors, is_mcp_error
from app.utils.settings.settings import MANAGED_DIR, SettingsLocations, get_settings_with_errors

PROMPT = "❯ "
#: `read_setup_line` when Esc was pressed.
ESCAPE = object()


class SetupDialogExit(TrustDialogExit):
    """
    A setup dialog's input closed: the process ends without a session, exit code 0. A
    `TrustDialogExit`, so the entry point (`app/main.py`) ends the process with its code the same way.
    """


async def read_setup_line(io: Any, escape: asyncio.Event) -> object:
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


@dataclass(frozen=True, slots=True)
class SetupDialogIO:
    """`DialogIO` on the terminal before the REPL opens: `escape` is set by its Esc key."""

    io: Any
    escape: asyncio.Event

    def write(self, text: str) -> None:
        self.io.write(text)

    async def ask(self, text: str, parse: Callable[[str], Any], retry: str, cancelled: Any) -> Any:
        self.io.write(text)
        while True:
            line = await read_setup_line(self.io, self.escape)
            if line is ESCAPE:
                return cancelled
            if line is None:
                raise SetupDialogExit(0)
            answer = parse(str(line))
            if answer is not None:
                return answer
            self.io.write(retry)


def setup_settings_locations(
    project: str | Path, *, home: str | Path | None = None, managed_dir: str | Path | None = None,
    settings_path: str | Path | None = None,
) -> SettingsLocations:
    """The settings the session will read (`run_settings_locations`), known before it opens."""
    return SettingsLocations(
        project_root=Path(project),
        home=Path(home) if home is not None else None,
        flag_settings_path=Path(settings_path) if settings_path is not None else None,
        managed_dir=Path(managed_dir if managed_dir is not None else MANAGED_DIR),
    )


async def show_mcpjson_server_approvals(io: Any, escape: asyncio.Event, locations: SettingsLocations) -> None:
    """`showSetupScreens`' MCP step: with every setting valid, the pending `.mcp.json` servers are asked."""
    if not get_settings_with_all_errors(locations):
        await handle_mcpjson_server_approvals(SetupDialogIO(io, escape), locations)


async def launch_invalid_settings_dialog(io: Any, escape: asyncio.Event, locations: SettingsLocations) -> None:
    """
    `main.tsx`' "Show settings validation errors after trust is established" with
    `dialogLaunchers.tsx` -> `launchInvalidSettingsDialog`: when the settings files have errors (the MCP
    configs' excluded: they do not keep settings from loading), the dialog asks; exiting ends the
    process with code 1 (`gracefulShutdownSync(1)`), continuing goes on without the invalid files.
    """
    errors = [error for error in get_settings_with_errors(locations)[1] if not is_mcp_error(error)]
    if errors and not await invalid_settings_dialog(SetupDialogIO(io, escape), errors):
        raise SetupDialogExit(1)


__all__ = [
    "ESCAPE",
    "PROMPT",
    "SetupDialogExit",
    "SetupDialogIO",
    "read_setup_line",
    "setup_settings_locations",
    "launch_invalid_settings_dialog",
    "show_mcpjson_server_approvals",
]
