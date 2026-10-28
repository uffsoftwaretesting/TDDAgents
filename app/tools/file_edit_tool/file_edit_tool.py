"""
Edit: exact string replacement in a file that was read first.

Ported from `reference/claude-code/src/tools/FileEditTool/FileEditTool.ts` -> `FileEditTool`;
input contract `FileEditInput` (`sdk-tools.d.ts`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.file_edit_tool.constants import FILE_UNEXPECTEDLY_MODIFIED_ERROR
from app.tools.file_edit_tool.utils import (
    apply_edit_to_file,
    find_actual_string,
    preserve_quote_style,
)
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    activate_conditional_skills,
    check_read_before_write,
    file_path_permission_matcher,
    get_prompt,
    invalid,
    is_stale,
    read_text,
    record_write,
    write_text,
)
from app.utils.file import FILE_NOT_FOUND_CWD_NOTE
from app.utils.permissions.filesystem import (
    check_file_write_permissions,
)

PROMPT_PATH = Path(__file__).with_name("prompt.md")


def build_edit_tool() -> BuiltTool:
    def validate_edit(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        file_path = str(input_args.get("file_path") or "")
        old_string = str(input_args.get("old_string", ""))
        new_string = str(input_args.get("new_string", ""))
        replace_all = bool(input_args.get("replace_all", False))

        if old_string == new_string:
            return invalid("No changes to make: old_string and new_string are exactly the same.", 1)
        ws = context.workspace
        if ws is None:
            return VALID  # call() reports the missing workspace

        content = read_text(ws, file_path, context)
        if content is None:
            if old_string == "":
                return VALID  # empty old_string on a missing file creates it
            return invalid(f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}", 4)
        if old_string == "":
            if content.strip() != "":
                return invalid("Cannot create new file - file already exists.", 3)
            return VALID

        precondition = check_read_before_write(context, file_path, content, not_read_code=6, modified_code=7)
        if not precondition.valid:
            return precondition

        actual = find_actual_string(content, old_string)
        if actual is None:
            return invalid(f"String to replace not found in file.\nString: {old_string}", 8)
        matches = content.count(actual)
        if matches > 1 and not replace_all:
            return invalid(
                f"Found {matches} matches of the string to replace, but replace_all is false. "
                "To replace all occurrences, set replace_all to true. To replace only one "
                "occurrence, please provide more context to uniquely identify the instance."
                f"\nString: {old_string}",
                9,
            )
        return VALID

    async def call_edit(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = str(input_args.get("file_path") or "")
        old_string = str(input_args.get("old_string", ""))
        new_string = str(input_args.get("new_string", ""))
        replace_all = bool(input_args.get("replace_all", False))
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        activate_conditional_skills(context, file_path)
        try:
            original = read_text(ws, file_path, context)
            if is_stale(context, file_path, original):
                return ToolResult(content=FILE_UNEXPECTEDLY_MODIFIED_ERROR, is_error=True)
            base = original or ""
            actual_old = find_actual_string(base, old_string) or old_string
            actual_new = preserve_quote_style(old_string, actual_old, new_string)
            updated = actual_new if actual_old == "" else apply_edit_to_file(base, actual_old, actual_new, replace_all)
            write_text(ws, file_path, updated, context)
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Error editing file: {e}", is_error=True)

        record_write(context, file_path, updated)
        if replace_all:
            return ToolResult(
                content=f"The file {file_path} has been updated. All occurrences were successfully replaced."
            )
        return ToolResult(content=f"The file {file_path} has been updated successfully.")

    return build_tool(
        name="Edit",
        prompt=get_prompt(PROMPT_PATH, "Edits a file."),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to modify"},
                "old_string": {"type": "string", "description": "The text to replace"},
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with (must be different from old_string)",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences of old_string (default false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        description=lambda args: f"Edit file: {args.get('file_path')}",
        is_destructive=lambda args: True,
        validate_input=validate_edit,
        check_permissions=check_file_write_permissions(),
        call=call_edit,
        prepare_permission_matcher=file_path_permission_matcher,
    )
