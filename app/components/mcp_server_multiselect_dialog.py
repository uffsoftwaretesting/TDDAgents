"""
The approval of several new `.mcp.json` servers at once.

Ported from `reference/claude-code/src/components/MCPServerMultiselectDialog.tsx` ->
`MCPServerMultiselectDialog` (its title and subtitle, `onSubmit`, `handleEscRejectAll`): the
selected servers join `enabledMcpjsonServers`, the others `disabledMcpjsonServers`, deduplicated,
in the project's local settings; Esc rejects every one. Every server starts selected
(`defaultValue={serverNames}`).

Upstream's `SelectMulti` toggles each server with Space and confirms with Enter. The REPL reads
lines (`app/components/design_system/dialog.py`), so the line names the servers to enable by
number (`1,3`), and an empty line confirms the starting selection, all of them; the hint under
the list says so in place of upstream's "Space to select · Enter to confirm · Esc to reject all".
Not ported: the `tengu_mcp_multidialog_choice` analytics event, and the dialog's warning color.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.components.design_system.dialog import DialogIO, render_options
from app.components.mcp_server_approval_dialog import listed_servers, write_local_settings
from app.components.mcp_server_dialog_copy import MCP_SERVER_DIALOG_COPY
from app.utils.settings.settings import SettingsLocations, get_initial_settings

SUBTITLE = "Select any you wish to enable."
#: What Esc answers: `handleEscRejectAll`.
REJECT_ALL = object()


def multiselect_dialog_title(server_names: Sequence[str]) -> str:
    """The dialog's title."""
    return f"{len(server_names)} new MCP servers found in .mcp.json"


def multiselect_hint(count: int) -> str:
    """The footer under the list: how a line selects, and what Esc does."""
    return f"Choose any of 1-{count} to enable, e.g. 1,3 · Enter to enable all · Esc to reject all"


def parse_selection(server_names: Sequence[str], line: str) -> list[str] | None:
    """The servers a line selects: every one for an empty line, else the numbered ones; `None` asks again."""
    tokens = line.replace(",", " ").split()
    if not tokens:
        return list(server_names)
    if not all(token.isdigit() and 1 <= int(token) <= len(server_names) for token in tokens):
        return None
    return [server_names[int(token) - 1] for token in dict.fromkeys(tokens)]


def on_submit(selected: Sequence[str], server_names: Sequence[str], locations: SettingsLocations) -> None:
    """`onSubmit`: the selected servers approved and the rest rejected, both deduplicated."""
    current = get_initial_settings(locations)
    approved = [server for server in server_names if server in selected]
    rejected = [server for server in server_names if server not in selected]
    if approved:
        enabled = listed_servers(current, "enabledMcpjsonServers")
        write_local_settings(locations, {"enabledMcpjsonServers": list(dict.fromkeys([*enabled, *approved]))})
    if rejected:
        disabled = listed_servers(current, "disabledMcpjsonServers")
        write_local_settings(locations, {"disabledMcpjsonServers": list(dict.fromkeys([*disabled, *rejected]))})


def reject_all(server_names: Sequence[str], locations: SettingsLocations) -> None:
    """`handleEscRejectAll`: every server joins `disabledMcpjsonServers`."""
    disabled = listed_servers(get_initial_settings(locations), "disabledMcpjsonServers")
    write_local_settings(locations, {"disabledMcpjsonServers": list(dict.fromkeys([*disabled, *server_names]))})


async def mcp_server_multiselect_dialog(
    io: DialogIO, server_names: Sequence[str], locations: SettingsLocations
) -> None:
    """The dialog, until the user confirms a selection or rejects every server with Esc."""
    text = "\n".join([multiselect_dialog_title(server_names), SUBTITLE, "", MCP_SERVER_DIALOG_COPY, "",
                      *render_options(server_names), multiselect_hint(len(server_names))])
    answer = await io.ask(text, lambda line: parse_selection(server_names, line),
                          f"Choose any of 1-{len(server_names)}, e.g. 1,3.", REJECT_ALL)
    if answer is REJECT_ALL:
        reject_all(server_names, locations)
    else:
        on_submit(answer, server_names, locations)


__all__ = [
    "REJECT_ALL",
    "SUBTITLE",
    "mcp_server_multiselect_dialog",
    "multiselect_dialog_title",
    "multiselect_hint",
    "on_submit",
    "parse_selection",
    "reject_all",
]
