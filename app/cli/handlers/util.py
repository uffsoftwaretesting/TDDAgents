"""
`python -m app.main doctor`: the `/doctor` screen without a session.

Ported from `reference/claude-code/src/main.tsx` (the `doctor` command: its description, no options) and
`src/cli/handlers/util.tsx` -> `doctorHandler` / `DoctorWithPlugins`: the `Doctor` screen
(`app/screens/doctor.py`) over a fresh app state, inside an MCP connection manager and with the plugins
loaded (`useManagePlugins`), then exit 0 once it is dismissed.

What the screen reads here, as upstream's providers give it: the process settings of the directory the
command runs in (`getOriginalCwd()`, `app/main.py` -> `plugin_cli_locations`); the MCP tools of every
configured server, the project's `.mcp.json` servers connected without the approval prompt (the
description's warning: "the workspace trust dialog is skipped and stdio servers from .mcp.json are
spawned"); the agents of that directory; the plugin loader's errors; and the empty permission context of
a fresh app state (`getEmptyToolPermissionContext`). The terminal is plain lines: the screen is printed,
and any line (or end of input) dismisses it.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO


#: `main.tsx`'s description of the command, with the product substitution.
DOCTOR_DESCRIPTION = (
    "Check the health of your TDDAgents auto-updater. Note: The workspace trust dialog is skipped and stdio "
    "servers from .mcp.json are spawned for health checks. Only use this command in directories you trust.")


class LineDialog:
    """The `DialogIO` of a plain terminal: text goes to `out`, each answer is one line of `inp`."""

    def __init__(self, inp: TextIO | None = None, out: TextIO | None = None) -> None:
        self._in = inp
        self._out = out

    def write(self, text: str) -> None:
        print(text, file=self._out or sys.stdout, flush=True)

    async def ask(self, text: str, parse: Callable[[str], Any], retry: str, cancelled: Any) -> Any:
        self.write(text)
        while True:
            line = await asyncio.to_thread((self._in or sys.stdin).readline)
            if not line:
                return cancelled
            answer = parse(line.rstrip("\n"))
            if answer is not None:
                return answer
            self.write(retry)


async def doctor_context(locations: Any, project: Path) -> tuple[Any, Any]:
    """The screen's context for `project`, and the MCP connection manager to close afterwards."""
    from app.cli.print import connect_mcp_servers
    from app.screens.doctor import DoctorContext
    from app.tools.agent_tool.load_agents_dir import get_agent_definitions_with_overrides, get_failed_agent_files
    from app.utils.claudemd import get_memory_files
    from app.utils.plugins.plugin_loader import load_all_plugins
    from app.workspace.local import LocalWorkspace

    workspace = LocalWorkspace(project)
    manager = await connect_mcp_servers(project, locations, is_non_interactive_session=True)
    plugins = await load_all_plugins(locations)
    definitions = get_agent_definitions_with_overrides(project_dir=project)
    context = DoctorContext(
        locations=locations, workspace=workspace, tools=list(manager.tools),
        active_agents=list(definitions.values()), plugin_errors=list(plugins.errors),
        memory_files=lambda: get_memory_files(workspace),
        failed_agent_files=lambda: get_failed_agent_files(project))
    return context, manager


async def doctor_handler(locations: Any, project: Path, io: Any | None = None) -> int:
    """`doctorHandler`: the screen until dismissed, the MCP connections closed, then exit code 0."""
    from app.commands.doctor.doctor import call

    context, manager = await doctor_context(locations, project)
    try:
        await call(io if io is not None else LineDialog(), context)
    finally:
        await manager.aclose()
    return 0


__all__ = ["DOCTOR_DESCRIPTION", "LineDialog", "doctor_context", "doctor_handler"]
