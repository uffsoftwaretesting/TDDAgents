"""
The dialog for ReadFile, Glob and Grep: `Read file` or `Edit file`, then `Name(input)`.

Ported from `reference/claude-code/src/components/permissions/FilesystemPermissionRequest/
FilesystemPermissionRequest.tsx`: the title follows `isReadOnly`, the content is
`{userFacingName}({renderToolUseMessage(input, verbose)})`, and a call with no path falls back to
`FallbackPermissionRequest`. No rule explanation (`FilePermissionDialog`).
"""

from __future__ import annotations

from typing import Any

from app.components.permissions.fallback_permission_request.fallback_permission_request import (
    fallback_permission_request,
)
from app.components.permissions.permission_dialog import PermissionDialog
from app.types.permissions import PermissionResult


def filesystem_permission_request(tool: Any, input_args: dict[str, Any], result: PermissionResult) -> PermissionDialog:
    path = input_args.get("file_path") or input_args.get("path")
    if not path:
        return fallback_permission_request(tool, input_args, result)
    verb = "Read" if tool.is_read_only(input_args) else "Edit"
    rendered = tool.render_tool_use_message(input_args, True) or ""
    return PermissionDialog(title=f"{verb} file", body=f"{tool.user_facing_name(input_args)}({rendered})")
