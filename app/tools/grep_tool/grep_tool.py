"""
Grep: content search through ripgrep, inside the workspace.

Ported from `reference/claude-code/src/tools/GrepTool/GrepTool.ts` -> `GrepTool`,
`applyHeadLimit`, `formatLimitInfo`; input contract `GrepInput` (`sdk-tools.d.ts`).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.utils import (
    NO_WORKSPACE,
    VALID,
    get_prompt,
    invalid,
    is_non_negative_int,
    path_kind,
    search_pattern_permission_matcher,
    search_target,
)
from app.utils.file import FILE_NOT_FOUND_CWD_NOTE
from app.utils.permissions.filesystem import (
    check_file_read_permissions,
    permission_cwd,
    read_deny_ignore_globs,
)
from app.utils.permissions.permissions import extract_permission_context
from app.utils.ripgrep import rip_grep, to_relative_path

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `GrepTool.ts` -> `DEFAULT_HEAD_LIMIT`; `head_limit=0` is the explicit unlimited escape.
DEFAULT_HEAD_LIMIT = 250

#: `GrepTool.ts` -> `VCS_DIRECTORIES_TO_EXCLUDE`.
VCS_DIRECTORIES_TO_EXCLUDE = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

#: `GrepTool.ts` passes `--max-columns 500` to ripgrep.
MAX_COLUMNS = 500


def split_grep_globs(glob: str) -> list[str]:
    """`GrepTool.call`: split on whitespace, then on commas unless the piece has braces."""
    patterns: list[str] = []
    for raw in glob.split():
        if "{" in raw and "}" in raw:
            patterns.append(raw)
        else:
            patterns.extend(p for p in raw.split(",") if p)
    return patterns


def apply_head_limit(items: list[str], limit: int | None, offset: int) -> tuple[list[str], int | None]:
    """`GrepTool.ts` -> `applyHeadLimit`: report the limit only when it truncated."""
    if limit == 0:
        return items[offset:], None
    effective = DEFAULT_HEAD_LIMIT if limit is None else limit
    truncated = len(items) - offset > effective
    return items[offset:offset + effective], (effective if truncated else None)


def format_limit_info(applied_limit: int | None, applied_offset: int) -> str:
    """`GrepTool.ts` -> `formatLimitInfo`."""
    parts: list[str] = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset:
        parts.append(f"offset: {applied_offset}")
    return ", ".join(parts)


#: Prints each path's mtime in ns (0 when the stat fails), for upstream's mtime sort.
MTIME_SCRIPT = (
    "import os,sys\n"
    "for p in sys.argv[1:]:\n"
    "    try: print(os.stat(p).st_mtime_ns)\n"
    "    except OSError: print(0)"
)


def build_grep_args(input_args: dict[str, Any], ignore_globs: list[str] | None = None) -> list[str]:
    """`GrepTool.call`: the ripgrep argv, flag for flag; `ignore_globs` hide Read-denied files."""
    mode = input_args.get("output_mode", "files_with_matches")
    pattern = str(input_args.get("pattern") or "")
    args = ["--hidden"]
    for d in VCS_DIRECTORIES_TO_EXCLUDE:
        args += ["--glob", f"!{d}"]
    args += ["--max-columns", str(MAX_COLUMNS)]
    if input_args.get("multiline"):
        args += ["-U", "--multiline-dotall"]
    if input_args.get("-i"):
        args.append("-i")
    if mode == "files_with_matches":
        args.append("-l")
    elif mode == "count":
        args.append("-c")
    if input_args.get("-n", True) and mode == "content":
        args.append("-n")
    if mode == "content":
        if input_args.get("context") is not None:
            args += ["-C", str(input_args["context"])]
        elif input_args.get("-C") is not None:
            args += ["-C", str(input_args["-C"])]
        else:
            if input_args.get("-B") is not None:
                args += ["-B", str(input_args["-B"])]
            if input_args.get("-A") is not None:
                args += ["-A", str(input_args["-A"])]
    args += ["-e", pattern] if pattern.startswith("-") else [pattern]
    if input_args.get("type"):
        args += ["--type", str(input_args["type"])]
    for g in split_grep_globs(str(input_args.get("glob") or "")):
        args += ["--glob", g]
    args += ignore_globs or []
    return args


def sort_by_mtime(ws: Any, paths: list[str]) -> list[str]:
    """Newest first, filename as tiebreak; a failed stat sorts as mtime 0 (`allSettled`)."""
    if not paths:
        return paths
    try:
        res = ws.execute(shlex.join(["python3", "-c", MTIME_SCRIPT, *paths]))
        mtimes = [int(x) for x in res.stdout.split()]
    except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
        mtimes = []
    if len(mtimes) != len(paths):
        mtimes = [0] * len(paths)
    return [p for p, _ in sorted(zip(paths, mtimes), key=lambda pm: (-pm[1], pm[0]))]


def _relativize_first_colon(line: str) -> str:
    i = line.find(":")
    return to_relative_path(line[:i]) + line[i:] if i > 0 else line


def _relativize_last_colon(line: str) -> str:
    i = line.rfind(":")
    return to_relative_path(line[:i]) + line[i:] if i > 0 else line


def build_grep_tool() -> BuiltTool:
    def validate_grep(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        path = input_args.get("path")
        ws = context.workspace
        if path and ws is not None and path_kind(ws, str(path)) is None:
            return invalid(f"Path does not exist: {path}. {FILE_NOT_FOUND_CWD_NOTE}", 1)
        for key in ("head_limit", "offset", "-A", "-B", "-C", "context"):
            if key in input_args and not is_non_negative_int(input_args[key]):
                return invalid(f"{key} must be a non-negative integer.", 0)
        mode = input_args.get("output_mode", "files_with_matches")
        if mode not in ("content", "files_with_matches", "count"):
            return invalid(f"Invalid output_mode: {mode}", 0)
        return VALID

    async def call_grep(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        mode = input_args.get("output_mode", "files_with_matches")
        head_limit = input_args.get("head_limit")
        offset = int(input_args.get("offset", 0))
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        target = search_target(ws, str(input_args.get("path") or "."))
        try:
            ignore = read_deny_ignore_globs(
                extract_permission_context(context), permission_cwd(context), anchor_floating=True
            )
            results = rip_grep(ws, build_grep_args(input_args, ignore), target)
        except Exception as e:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
            return ToolResult(content=f"Grep error: {e}", is_error=True)

        if mode == "content":
            items, applied = apply_head_limit(results, head_limit, offset)
            body = "\n".join(_relativize_first_colon(line) for line in items) or "No matches found"
            info = format_limit_info(applied, offset)
            return ToolResult(content=f"{body}\n\n[Showing results with pagination = {info}]" if info else body)

        if mode == "count":
            items, applied = apply_head_limit(results, head_limit, offset)
            lines = [_relativize_last_colon(line) for line in items]
            total = n_files = 0
            for line in lines:
                i = line.rfind(":")
                if i > 0 and line[i + 1:].strip().lstrip("-").isdigit():
                    total += int(line[i + 1:])
                    n_files += 1
            info = format_limit_info(applied, offset)
            summary = (
                f"\n\nFound {total} total {'occurrence' if total == 1 else 'occurrences'} across "
                f"{n_files} {'file' if n_files == 1 else 'files'}."
                f"{f' with pagination = {info}' if info else ''}"
            )
            return ToolResult(content=("\n".join(lines) or "No matches found") + summary)

        items, applied = apply_head_limit(sort_by_mtime(ws, results), head_limit, offset)
        if not items:
            return ToolResult(content="No files found")
        info = format_limit_info(applied, offset)
        names = [to_relative_path(p) for p in items]
        header = f"Found {len(names)} {'file' if len(names) == 1 else 'files'}{f' {info}' if info else ''}"
        return ToolResult(content=header + "\n" + "\n".join(names))

    return build_tool(
        name="Grep",
        prompt=get_prompt(PROMPT_PATH, "Searches file contents."),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regular expression pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (rg PATH). Defaults to the workspace root.",
                },
                "glob": {
                    "type": "string",
                    "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": 'Output mode. Defaults to "files_with_matches".',
                },
                "-B": {"type": "integer", "description": "Lines to show before each match (rg -B). Content mode."},
                "-A": {"type": "integer", "description": "Lines to show after each match (rg -A). Content mode."},
                "-C": {"type": "integer", "description": "Alias for context."},
                "context": {
                    "type": "integer",
                    "description": "Lines to show before and after each match (rg -C). Content mode.",
                },
                "-n": {"type": "boolean", "description": "Show line numbers (rg -n). Content mode; defaults to true."},
                "-i": {"type": "boolean", "description": "Case insensitive search (rg -i)"},
                "type": {
                    "type": "string",
                    "description": "File type to search (rg --type). Common types: js, py, rust, go, java, etc.",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Limit output to first N lines/entries. Defaults to 250; 0 for unlimited.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N lines/entries before applying head_limit. Defaults to 0.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": "Enable multiline mode where . matches newlines (rg -U --multiline-dotall).",
                },
            },
            "required": ["pattern"],
        },
        description=lambda args: f"Grep: {args.get('pattern')}",
        is_read_only=lambda args: True,
        is_concurrency_safe=lambda args: True,
        validate_input=validate_grep,
        check_permissions=check_file_read_permissions("path"),
        call=call_grep,
        prepare_permission_matcher=search_pattern_permission_matcher,
    )
