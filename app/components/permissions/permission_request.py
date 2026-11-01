"""
Which dialog a permission 'ask' shows.

Ported from `reference/claude-code/src/components/permissions/PermissionRequest.tsx` ->
`permissionComponentForTool`, over TDDAgents' tool names: Bash, Edit, WriteFile, WebFetch,
Skill and EnterPlanMode have their own dialogs; ReadFile, Glob and Grep share the filesystem
dialog; every other tool (MCP included) gets the fallback. ExitPlanMode, UpdatePlan and
AskUserQuestion are handled before this, by their own flows in `use_can_use_tool`.
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.bash_permission_request.bash_permission_request import (
    bash_permission_request,
)
from app.components.permissions.enter_plan_mode_permission_request.enter_plan_mode_permission_request import (
    enter_plan_mode_permission_request,
)
from app.components.permissions.fallback_permission_request.fallback_permission_request import (
    fallback_permission_request,
)
from app.components.permissions.file_edit_permission_request.file_edit_permission_request import (
    file_edit_permission_request,
)
from app.components.permissions.file_write_permission_request.file_write_permission_request import (
    file_write_permission_request,
)
from app.components.permissions.filesystem_permission_request.filesystem_permission_request import (
    filesystem_permission_request,
)
from app.components.permissions.permission_dialog import PermissionDialog
from app.components.permissions.skill_permission_request.skill_permission_request import (
    skill_permission_request,
)
from app.components.permissions.web_fetch_permission_request.web_fetch_permission_request import (
    web_fetch_permission_request,
)
from app.types.permissions import PermissionResult

#: `FilesystemPermissionRequest` serves the read and search tools (`GlobTool`, `GrepTool`, `FileReadTool`).
FILESYSTEM_TOOL_NAMES = frozenset({"ReadFile", "Glob", "Grep"})


def permission_dialog(
    tool: Any, input_args: dict[str, Any], result: PermissionResult, context: Any
) -> PermissionDialog:
    """`permissionComponentForTool(tool)` rendered for this call."""
    name = tool.name
    if name == "Bash":
        return bash_permission_request(tool, input_args, result)
    if name == "Edit":
        return file_edit_permission_request(input_args, context)
    if name == "WriteFile":
        return file_write_permission_request(input_args, context)
    if name == "WebFetch":
        return web_fetch_permission_request(tool, input_args, result)
    if name == "Skill":
        return skill_permission_request(input_args, result, context)
    if name == "EnterPlanMode":
        return enter_plan_mode_permission_request()
    if name in FILESYSTEM_TOOL_NAMES:
        return filesystem_permission_request(tool, input_args, result)
    return fallback_permission_request(tool, input_args, result)
