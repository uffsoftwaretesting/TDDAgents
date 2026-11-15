"""
WriteFile: create or overwrite a file (an existing one must have been read first).

Ported from `reference/claude-code/src/tools/FileWriteTool/FileWriteTool.ts` -> `FileWriteTool`
(`call`'s `Output`: `type` `create` | `update`, `filePath`, `content`, `structuredPatch`,
`originalFile`, carried as the result's `metadata`, its `tool_use_result`;
`mapToolResultToToolResultBlockParam`); input contract `FileWriteInput` (`sdk-tools.d.ts`). As
upstream's `if (oldContent)`, an existing empty file is a `create`. Not ported: `gitDiff` (remote
sessions only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.file_edit_tool.constants import FILE_UNEXPECTEDLY_MODIFIED_ERROR
from app.tools.file_write_tool import ui
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    activate_conditional_skills,
    check_read_before_write,
    file_path_permission_matcher,
    get_prompt,
    is_stale,
    read_text,
    record_write,
    track_file_history_edit,
    write_text,
)
from app.utils.diff import FileEdit, get_structured_patch_for_display
from app.utils.path import backfill_path
from app.utils.permissions.filesystem import (
    check_file_write_permissions,
)

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def build_write_file_tool() -> BuiltTool:
    def validate_write(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        ws = context.workspace
        if ws is None:
            return VALID  # call() reports the missing workspace
        path = str(input_args.get("file_path") or "")
        current = read_text(ws, path, context)
        if current is None:
            return VALID
        return check_read_before_write(context, path, current, not_read_code=2, modified_code=3)

    async def call_write(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = str(input_args.get("file_path") or "")
        content = str(input_args.get("content", ""))
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        activate_conditional_skills(context, file_path)
        # `fileHistoryTrackEdit` before the write: the backup holds the pre-edit content.
        await track_file_history_edit(context, file_path)
        try:
            existing = read_text(ws, file_path, context)
            if is_stale(context, file_path, existing):
                return ToolResult(content=FILE_UNEXPECTEDLY_MODIFIED_ERROR, is_error=True)
            write_text(ws, file_path, content, context)
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
        record_write(context, file_path, content)
        if existing:
            patch = get_structured_patch_for_display(existing, [FileEdit(existing, content)])
            return ToolResult(
                content=f"The file {file_path} has been updated successfully.",
                metadata={"type": "update", "filePath": file_path, "content": content,
                          "structuredPatch": patch, "originalFile": existing},
            )
        return ToolResult(
            content=f"File created successfully at: {file_path}",
            metadata={"type": "create", "filePath": file_path, "content": content,
                      "structuredPatch": [], "originalFile": None},
        )

    return build_tool(
        user_facing_name=ui.user_facing_name,
        render_tool_use_message=ui.render_tool_use_message,
        render_tool_result_message=ui.render_tool_result_message,
        get_tool_use_summary=ui.get_tool_use_summary,
        get_activity_description=ui.get_activity_description,
        backfill_observable_input=backfill_path("file_path"),
        name="WriteFile",
        prompt=get_prompt(PROMPT_PATH, "Writes a file."),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["file_path", "content"],
        },
        description=lambda args: f"Write file: {args.get('file_path')}",
        is_destructive=lambda args: True,
        validate_input=validate_write,
        check_permissions=check_file_write_permissions(),
        call=call_write,
        prepare_permission_matcher=file_path_permission_matcher,
    )
