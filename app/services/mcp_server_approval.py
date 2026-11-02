"""
Asking about the project's pending `.mcp.json` servers before an interactive session opens.

Ported from `reference/claude-code/src/services/mcpServerApproval.tsx` ->
`handleMcpjsonServerApprovals`: the project scope's servers whose status is `pending`
(`getProjectMcpServerStatus`, `app/services/mcp/config.py`), one dialog for a single server
(`app/components/mcp_server_approval_dialog.py`), the multi-select for several
(`app/components/mcp_server_multiselect_dialog.py`), nothing when none is pending. Upstream
renders them on the Ink root `main.tsx` made; here they are asked through a `DialogIO`
(`app/interactive_helpers.py` gives the terminal's before the REPL exists).
"""

from __future__ import annotations

from app.components.design_system.dialog import DialogIO
from app.components.mcp_server_approval_dialog import mcp_server_approval_dialog
from app.components.mcp_server_multiselect_dialog import mcp_server_multiselect_dialog
from app.services.mcp.config import get_mcp_configs_by_scope, get_project_mcp_server_status
from app.utils.settings.settings import SettingsLocations


def pending_project_servers(locations: SettingsLocations) -> list[str]:
    """
    The `.mcp.json` servers nobody has approved or rejected yet. Only an interactive session
    asks (a non-interactive one approves them), so the status is read as an interactive one's.
    """
    servers, _ = get_mcp_configs_by_scope("project", locations.project_root, locations)
    return [name for name in servers
            if get_project_mcp_server_status(name, locations, is_non_interactive_session=False) == "pending"]


async def handle_mcpjson_server_approvals(io: DialogIO, locations: SettingsLocations) -> None:
    """`handleMcpjsonServerApprovals`: the approval dialog for the pending servers, if any."""
    pending = pending_project_servers(locations)
    if not pending:
        return
    if len(pending) == 1:
        await mcp_server_approval_dialog(io, pending[0], locations)
    else:
        await mcp_server_multiselect_dialog(io, pending, locations)


__all__ = ["handle_mcpjson_server_approvals", "pending_project_servers"]
