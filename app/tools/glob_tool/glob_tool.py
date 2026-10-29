"""
Glob: file-pattern search through ripgrep, inside the workspace.

Ported from `reference/claude-code/src/tools/GlobTool/GlobTool.ts` -> `GlobTool`; input
contract `GlobInput` (`sdk-tools.d.ts`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    get_prompt,
    invalid,
    path_kind,
    search_pattern_permission_matcher,
    search_target,
)
from app.utils.file import FILE_NOT_FOUND_CWD_NOTE
from app.utils.glob import extract_glob_base_directory
from app.utils.permissions.filesystem import (
    check_file_read_permissions,
    permission_cwd,
    read_deny_ignore_globs,
)
from app.utils.permissions.permissions import extract_permission_context
from app.utils.ripgrep import rip_grep, to_relative_path

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `GlobTool.ts` -> `globLimits?.maxResults ?? 100`.
GLOB_MAX_RESULTS = 100


def build_glob_tool() -> BuiltTool:
    def validate_glob(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        path = input_args.get("path")
        ws = context.workspace
        if not path or ws is None:
            return VALID
        kind = path_kind(ws, str(path))
        if kind is None:
            return invalid(f"Directory does not exist: {path}. {FILE_NOT_FOUND_CWD_NOTE}", 1)
        if kind != "dir":
            return invalid(f"Path is not a directory: {path}", 2)
        return VALID

    async def call_glob(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = str(input_args.get("pattern") or "")
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        search_dir = search_target(ws, str(input_args.get("path") or "."))
        if pattern.startswith("/"):
            # An absolute pattern names its own base directory, as upstream's `extractGlobBaseDirectory`.
            base, pattern = extract_glob_base_directory(pattern)
            search_dir = search_target(ws, base)
        args = ["--files", "--glob", pattern, "--sort=modified", "--no-ignore", "--hidden"]
        args += read_deny_ignore_globs(
            extract_permission_context(context), permission_cwd(context), anchor_floating=False
        )
        try:
            paths = [to_relative_path(p) for p in rip_grep(ws, args, search_dir)]
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Glob error: {e}", is_error=True)
        if not paths:
            return ToolResult(content="No files found")
        lines = paths[:GLOB_MAX_RESULTS]
        if len(paths) > GLOB_MAX_RESULTS:
            lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
        return ToolResult(content="\n".join(lines))

    return build_tool(
        name="Glob",
        prompt=get_prompt(PROMPT_PATH, "Finds files."),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The glob pattern to match files against"},
                "path": {
                    "type": "string",
                    "description": "The directory to search in. If not specified, the workspace root is used.",
                },
            },
            "required": ["pattern"],
        },
        description=lambda args: f"Glob: {args.get('pattern')}",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate_glob,
        check_permissions=check_file_read_permissions("path"),
        call=call_glob,
        prepare_permission_matcher=search_pattern_permission_matcher,
    )
