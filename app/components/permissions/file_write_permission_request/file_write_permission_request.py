"""
The WriteFile dialog: create or overwrite, and what the file will hold.

Ported from `reference/claude-code/src/components/permissions/FileWritePermissionRequest/
FileWritePermissionRequest.tsx` and `FileWriteToolDiff.tsx`: title `Create file` or
`Overwrite file`, the relative path as subtitle, the new content (a new file) or the patch from
the old content (an overwrite), and "Do you want to create|overwrite <name>?". No rule
explanation, as upstream's `FilePermissionDialog`.
"""

from __future__ import annotations

import os
from typing import Any

from app.components.permissions.file_edit_permission_request.file_edit_permission_request import (
    current_content,
    relative_path,
)
from app.components.permissions.permission_dialog import PermissionDialog
from app.utils.diff import get_patch_from_contents, render_hunks
from app.utils.permissions.filesystem import permission_cwd


def file_write_permission_request(input_args: dict[str, Any], context: Any) -> PermissionDialog:
    file_path = str(input_args.get("file_path") or "")
    content = str(input_args.get("content", ""))
    old_content = current_content(file_path, context)
    exists = old_content is not None
    action = "overwrite" if exists else "create"
    body = render_hunks(get_patch_from_contents(old_content, content)) if old_content is not None else content
    return PermissionDialog(
        title="Overwrite file" if exists else "Create file",
        subtitle=relative_path(file_path, permission_cwd(context)),
        body=body,
        question=f"Do you want to {action} {os.path.basename(file_path)}?",
    )
