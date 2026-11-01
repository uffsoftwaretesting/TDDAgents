"""
The Edit dialog: the file, the patch, and "Do you want to make this edit to <name>?".

Ported from `reference/claude-code/src/components/permissions/FileEditPermissionRequest/
FileEditPermissionRequest.tsx` (title `Edit file`, the path relative to the working directory as
subtitle, `FileEditToolDiff`'s patch as content) over `FilePermissionDialog`, which shows no
rule explanation. The patch is computed from the file as it is now (`getPatchForDisplay`); a
file that cannot be read is diffed from empty.
"""

from __future__ import annotations

import os
from typing import Any

from app.components.permissions.permission_dialog import PermissionDialog
from app.tools.utils import read_text
from app.utils.diff import FileEdit, get_patch_for_display, render_hunks
from app.utils.permissions.filesystem import permission_cwd
from app.workspace.base import WorkspaceError


def current_content(file_path: str, context: Any) -> str | None:
    """The file as it is now, or `None` when there is none or it cannot be read (the dialog shows
    the change from empty, as upstream's `readFileSync` catch does)."""
    workspace = getattr(context, "workspace", None)
    if workspace is None:
        return None
    try:
        return read_text(workspace, file_path, context)
    except WorkspaceError:
        return None


def relative_path(file_path: str, cwd: str) -> str:
    """`relative(getCwd(), file_path)`."""
    absolute = file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
    return os.path.relpath(absolute, cwd)


def file_edit_permission_request(input_args: dict[str, Any], context: Any) -> PermissionDialog:
    file_path = str(input_args.get("file_path") or "")
    current = current_content(file_path, context)
    edit = FileEdit(str(input_args.get("old_string", "")), str(input_args.get("new_string", "")),
                    bool(input_args.get("replace_all", False)))
    hunks = get_patch_for_display(current or "", [edit])
    return PermissionDialog(
        title="Edit file",
        subtitle=relative_path(file_path, permission_cwd(context)),
        body=render_hunks(hunks),
        question=f"Do you want to make this edit to {os.path.basename(file_path)}?",
    )
