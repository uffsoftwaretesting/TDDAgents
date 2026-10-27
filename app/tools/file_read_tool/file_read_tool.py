"""
ReadFile: a file's lines, numbered, from a 1-based offset.

Ported from `reference/claude-code/src/tools/FileReadTool/FileReadTool.ts` -> `FileReadTool`;
input contract `FileReadInput` (`sdk-tools.d.ts`).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    activate_conditional_skills,
    file_path_permission_matcher,
    get_prompt,
    invalid,
    is_non_negative_int,
    read_text,
)
from app.utils.file import FILE_NOT_FOUND_CWD_NOTE, add_line_numbers
from app.utils.file_state_cache import FileState
from app.utils.permissions.filesystem import (
    check_file_read_permissions,
    matching_rule_for_input,
    permission_cwd,
    workspace_absolute_path,
)
from app.utils.permissions.permissions import extract_permission_context
from app.workspace.base import normalize_path

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `FileReadTool.validateInput`'s refusal of a Read-denied path (errorCode 1).
READ_DENIED_MESSAGE = "File is in a directory that is denied by your permission settings."

#: `FileReadTool/prompt.ts` -> `MAX_LINES_TO_READ`.
MAX_LINES_TO_READ = 2000


def build_read_file_tool() -> BuiltTool:
    def validate_read(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        # `FileReadTool.validateInput`: a Read-deny match is refused before any I/O.
        cwd = permission_cwd(context)
        target = workspace_absolute_path(str(input_args.get("file_path") or ""), cwd)
        if matching_rule_for_input(target, extract_permission_context(context), "read", "deny", cwd) is not None:
            return invalid(READ_DENIED_MESSAGE, 1)
        if "offset" in input_args and not is_non_negative_int(input_args["offset"]):
            return invalid("offset must be a non-negative integer.", 0)
        limit = input_args.get("limit")
        if "limit" in input_args and not (is_non_negative_int(limit) and limit != 0):
            return invalid("limit must be a positive integer.", 0)
        return VALID

    async def call_read(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = str(input_args.get("file_path") or "")
        offset = int(input_args.get("offset", 1))
        limit = input_args.get("limit")
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        try:
            content = read_text(ws, file_path)
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Error reading file: {e}", is_error=True)
        if content is None:
            return ToolResult(content=f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE}", is_error=True)

        all_lines = content.splitlines()
        line_offset = 0 if offset == 0 else offset - 1
        window = all_lines[line_offset:line_offset + (limit if limit is not None else MAX_LINES_TO_READ)]
        partial = "offset" in input_args or "limit" in input_args
        context.read_file_state[normalize_path(file_path)] = FileState(
            content=content,
            offset=offset if partial else None,
            limit=limit if partial else None,
        )
        # `FileReadTool`: the file's directories may hold nested memory to attach next turn.
        context.nested_memory_attachment_triggers.add(normalize_path(file_path))
        activate_conditional_skills(context, file_path)

        if window:
            return ToolResult(content=add_line_numbers(window, max(offset, 1)))
        if not all_lines:
            return ToolResult(
                content="<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
            )
        return ToolResult(
            content=(
                "<system-reminder>Warning: the file exists but is shorter than the provided offset "
                f"({offset}). The file has {len(all_lines)} lines.</system-reminder>"
            )
        )

    return build_tool(
        name="ReadFile",
        prompt=get_prompt(PROMPT_PATH, "Reads a file."),
        # `FileReadTool.ts`: Infinity, because persisting a read would create a
        # ReadFile -> file -> ReadFile loop and the tool already bounds its own output.
        max_result_size_chars=math.inf,
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
                "offset": {
                    "type": "integer",
                    "description": "The line number to start reading from. "
                    "Only provide if the file is too large to read at once",
                },
                "limit": {
                    "type": "integer",
                    "description": "The number of lines to read. "
                    "Only provide if the file is too large to read at once.",
                },
            },
            "required": ["file_path"],
        },
        description=lambda args: f"Read file: {args.get('file_path')}",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate_read,
        check_permissions=check_file_read_permissions("file_path"),
        call=call_read,
        prepare_permission_matcher=file_path_permission_matcher,
    )
