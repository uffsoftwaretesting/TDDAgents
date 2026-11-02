"""
The approval of the one new `.mcp.json` server a project has.

Ported from `reference/claude-code/src/components/MCPServerApprovalDialog.tsx` ->
`MCPServerApprovalDialog` (its title, its three options and `onChange`): "yes" adds the server to
`enabledMcpjsonServers`, "yes_all" also sets `enableAllProjectMcpServers`, "no" (and Esc) adds it
to `disabledMcpjsonServers`, each written to the project's local settings
(`<project>/.tddagents/settings.local.json`). The lists are read from the merged settings, as
upstream's `getSettings_DEPRECATED()`. The options are picked by number
(`app/components/design_system/dialog.py`). Not ported: the `tengu_mcp_dialog_choice` analytics
event, and the dialog's warning color.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from app.components.design_system.dialog import Cancelled, DialogIO, select
from app.components.mcp_server_dialog_copy import MCP_SERVER_DIALOG_COPY
from app.types.permissions import PermissionRuleSource
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.settings.settings import SettingsLocations, get_initial_settings

logger = logging.getLogger(__name__)

ApprovalChoice = Literal["yes_all", "yes", "no"]

#: `<Select options>`, in upstream's order.
CHOICES: tuple[tuple[ApprovalChoice, str], ...] = (
    ("yes_all", "Use this and all future MCP servers in this project"),
    ("yes", "Use this MCP server"),
    ("no", "Continue without using this MCP server"),
)


def approval_dialog_title(server_name: str) -> str:
    """The dialog's title."""
    return f"New MCP server found in .mcp.json: {server_name}"


def listed_servers(settings: Mapping[str, Any], key: str) -> list[str]:
    """`currentSettings.<key> || []`: a settings list of server names."""
    value = settings.get(key)
    return [str(name) for name in value] if isinstance(value, list) else []


def write_local_settings(locations: SettingsLocations, patch: Mapping[str, Any]) -> None:
    """`updateSettingsForSource('localSettings', patch)`; a failed write is logged, as upstream's."""
    error = update_settings_for_source(PermissionRuleSource.LOCAL_SETTINGS, locations, patch)
    if error is not None:
        logger.error("%s", error)


def on_change(value: ApprovalChoice, server_name: str, locations: SettingsLocations) -> None:
    """`onChange`: the choice saved in the project's local settings."""
    current = get_initial_settings(locations)
    if value in ("yes", "yes_all"):
        enabled = listed_servers(current, "enabledMcpjsonServers")
        if server_name not in enabled:
            write_local_settings(locations, {"enabledMcpjsonServers": [*enabled, server_name]})
        if value == "yes_all":
            write_local_settings(locations, {"enableAllProjectMcpServers": True})
        return
    disabled = listed_servers(current, "disabledMcpjsonServers")
    if server_name not in disabled:
        write_local_settings(locations, {"disabledMcpjsonServers": [*disabled, server_name]})


async def mcp_server_approval_dialog(io: DialogIO, server_name: str, locations: SettingsLocations) -> None:
    """The dialog, until the user picks an option; Esc is "no" (`onCancel={() => onChange('no')}`)."""
    lines = [approval_dialog_title(server_name), "", MCP_SERVER_DIALOG_COPY, ""]
    choice = await select(io, lines, [label for _, label in CHOICES], "cancel")
    on_change("no" if isinstance(choice, Cancelled) else CHOICES[choice][0], server_name, locations)


__all__ = [
    "CHOICES",
    "ApprovalChoice",
    "approval_dialog_title",
    "listed_servers",
    "mcp_server_approval_dialog",
    "on_change",
    "write_local_settings",
]
