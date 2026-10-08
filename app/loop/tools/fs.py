"""
The loop's file primitives: `ReadFile`, `WriteFile`, `Edit`, `Glob`, `Grep`.

Ported from claude-code v2.1.88; input contracts follow `reference/claude-code/sdk-tools.d.ts`
(`FileReadInput`, `FileWriteInput`, `FileEditInput`, `GlobInput`, `GrepInput`):

* `src/tools/FileReadTool/FileReadTool.ts` -> `FileReadTool` (1-based `offset`, line-numbered
  output via `src/utils/file.ts` -> `addLineNumbers`, compact `N<TAB>line` form)
* `src/tools/FileWriteTool/FileWriteTool.ts` -> `FileWriteTool`
* `src/tools/FileEditTool/FileEditTool.ts` -> `FileEditTool`, with `findActualString`,
  `preserveQuoteStyle` and `applyEditToFile` from `FileEditTool/utils.ts`
* `src/tools/GlobTool/GlobTool.ts` -> `GlobTool`, `src/utils/glob.ts` -> `glob`
* `src/tools/GrepTool/GrepTool.ts` -> `GrepTool`, `applyHeadLimit`, `formatLimitInfo`

Preconditions live in `validate_input` with upstream's `errorCode`s, so they run in the
same pipeline position as upstream (`run_tool_use`: resolve -> validate -> permission ->
call). Read-before-write uses `ToolContext.read_file_state` (`app/loop/context/file_state.py`).

Every operation goes through the `Workspace` protocol, so the same tool works against the
sandbox and the host, and the workspace's path containment is the boundary.

`WriteFile` and `Edit` are *generic* writers (`GENERIC_WRITER_TOOL_NAMES` in
`app/loop/permissions/tdd.py`): the TDD gate classifies each call by its target path. They
must not claim `is_implementation_writer` / `is_test_writer`, because pool assembly strips
tools carrying those flags wholesale — which would remove every write primitive.

Glob and Grep run ripgrep inside the workspace exactly as upstream does
(`app/loop/tools/ripgrep.py`), with the same argv.

Divergences, each forced by the `Workspace` protocol:

* Staleness is decided by content comparison, not timestamps (see `file_state.py`).
* Paths are workspace-relative; an absolute Glob pattern is re-rooted at the workspace.
* Permission-derived read-ignore globs (`getFileReadIgnorePatterns`) and the plugin-cache
  exclusions are not passed to rg: neither concept exists here yet.
* Read omits images, PDFs (`pages`) and notebooks.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from app.loop.context import ToolContext
from app.loop.context.file_state import FileState
from app.loop.prompts.loader import parse_markdown_frontmatter, render_prompt
from app.loop.prompts.registry import global_prompt_registry
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.ripgrep import rip_grep, to_relative_path
from app.loop.tools.types import ToolResult, ValidationResult
from app.workspace.base import WorkspaceNotFound, normalize_path

#: `FileReadTool/prompt.ts` -> `MAX_LINES_TO_READ`.
MAX_LINES_TO_READ = 2000

#: `GlobTool.ts` -> `globLimits?.maxResults ?? 100`.
GLOB_MAX_RESULTS = 100

#: `GrepTool.ts` -> `DEFAULT_HEAD_LIMIT`; `head_limit=0` is the explicit unlimited escape.
DEFAULT_HEAD_LIMIT = 250

#: `GrepTool.ts` -> `VCS_DIRECTORIES_TO_EXCLUDE`.
VCS_DIRECTORIES_TO_EXCLUDE = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

#: `GrepTool.ts` passes `--max-columns 500` to ripgrep.
MAX_COLUMNS = 500

#: `src/utils/file.ts` -> `FILE_NOT_FOUND_CWD_NOTE`; our cwd is the workspace root.
FILE_NOT_FOUND_CWD_NOTE = "Note: your current working directory is the workspace root."

FILE_NOT_READ_ERROR = "File has not been read yet. Read it first before writing to it."
FILE_MODIFIED_SINCE_READ_ERROR = (
    "File has been modified since read, either by the user or by a linter. "
    "Read it again before attempting to write it."
)
#: `FileEditTool/constants.ts` -> `FILE_UNEXPECTEDLY_MODIFIED_ERROR`.
FILE_UNEXPECTEDLY_MODIFIED_ERROR = "File has been unexpectedly modified. Read it again before attempting to write it."

LEFT_SINGLE_CURLY_QUOTE = "‘"
RIGHT_SINGLE_CURLY_QUOTE = "’"
LEFT_DOUBLE_CURLY_QUOTE = "“"
RIGHT_DOUBLE_CURLY_QUOTE = "”"

NO_WORKSPACE = ToolResult(content="No workspace available", is_error=True)
VALID = ValidationResult(valid=True)


def get_prompt(filename: str, fallback: str, vars: Mapping[str, Any] | None = None) -> str:
    path = Path(__file__).parent.parent.parent / "prompts" / "tools" / filename
    if path.exists():
        raw_body = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))[1]
        if vars:
            return render_prompt(raw_body, vars)
        return raw_body

    ctx_vars = dict(vars or {})
    desc = global_prompt_registry.get_tool_description(filename, ctx_vars)
    if desc:
        return desc
    if vars:
        return render_prompt(fallback, ctx_vars)
    return fallback


def invalid(message: str, code: int) -> ValidationResult:
    return ValidationResult(valid=False, message=message, error_code=code)


# ── shared helpers ───────────────────────────────────────────────────────────

def read_text(ws: Any, path: str) -> str | None:
    """File content with CRLF normalized (as `readFileForEdit` does), or None if missing."""
    try:
        content: str = ws.read_file(path)
    except WorkspaceNotFound:
        return None
    return content.replace("\r\n", "\n")


def check_read_before_write(
    context: ToolContext, path: str, current: str, not_read_code: int, modified_code: int
) -> ValidationResult:
    """The `readFileState` precondition shared by `FileWriteTool` and `FileEditTool`."""
    state = context.read_file_state.get(normalize_path(path))
    if state is None or state.is_partial_view:
        return invalid(FILE_NOT_READ_ERROR, not_read_code)
    if state.content != current:
        return invalid(FILE_MODIFIED_SINCE_READ_ERROR, modified_code)
    return VALID


def is_stale(context: ToolContext, path: str, current: str | None) -> bool:
    """Call-time recheck: upstream re-validates between validation and the write."""
    if current is None:
        return False
    state = context.read_file_state.get(normalize_path(path))
    return state is None or state.content != current


def record_write(context: ToolContext, path: str, content: str) -> None:
    """Edit/Write store `offset=undefined`, so the next edit needs no fresh Read."""
    context.read_file_state[normalize_path(path)] = FileState(content=content)


def split_grep_globs(glob: str) -> list[str]:
    """`GrepTool.call`: split on whitespace, then on commas unless the piece has braces."""
    patterns: list[str] = []
    for raw in glob.split():
        if "{" in raw and "}" in raw:
            patterns.append(raw)
        else:
            patterns.extend(p for p in raw.split(",") if p)
    return patterns


def path_kind(ws: Any, path: str) -> str | None:
    """'dir', 'file', or None if absent — decided through the protocol alone."""
    normalized = normalize_path(path or ".")
    if normalized == ".":
        return "dir"
    if not ws.exists(normalized):
        return None
    parent = normalized.rsplit("/", 1)[0] if "/" in normalized else "."
    for entry in ws.list_files(parent, depth=1):
        if entry.path == normalized:
            return "dir" if entry.is_dir else "file"
    return "file"


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


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# ── Edit helpers (FileEditTool/utils.ts) ─────────────────────────────────────

def normalize_quotes(text: str) -> str:
    return (
        text.replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"')
    )


def find_actual_string(file_content: str, search: str) -> str | None:
    """Exact match first, then a match under curly-quote normalization."""
    if search in file_content:
        return search
    index = normalize_quotes(file_content).find(normalize_quotes(search))
    if index != -1:
        return file_content[index:index + len(search)]
    return None


def _is_opening_context(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    return chars[index - 1] in (" ", "\t", "\n", "\r", "(", "[", "{", "—", "–")


def _apply_curly_double_quotes(text: str) -> str:
    chars = list(text)
    return "".join(
        (LEFT_DOUBLE_CURLY_QUOTE if _is_opening_context(chars, i) else RIGHT_DOUBLE_CURLY_QUOTE)
        if c == '"' else c
        for i, c in enumerate(chars)
    )


def _apply_curly_single_quotes(text: str) -> str:
    chars = list(text)
    out: list[str] = []
    for i, c in enumerate(chars):
        if c != "'":
            out.append(c)
            continue
        prev = chars[i - 1] if i > 0 else ""
        nxt = chars[i + 1] if i < len(chars) - 1 else ""
        if prev.isalpha() and nxt.isalpha():
            out.append(RIGHT_SINGLE_CURLY_QUOTE)  # contraction, e.g. don't
        else:
            out.append(LEFT_SINGLE_CURLY_QUOTE if _is_opening_context(chars, i) else RIGHT_SINGLE_CURLY_QUOTE)
    return "".join(out)


def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """Re-apply the file's curly quotes to `new_string` when the match needed normalizing."""
    if old_string == actual_old_string:
        return new_string
    result = new_string
    if LEFT_DOUBLE_CURLY_QUOTE in actual_old_string or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string:
        result = _apply_curly_double_quotes(result)
    if LEFT_SINGLE_CURLY_QUOTE in actual_old_string or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string:
        result = _apply_curly_single_quotes(result)
    return result


def apply_edit_to_file(original: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Deleting a whole line (empty `new_string`) also removes its trailing newline."""
    count = -1 if replace_all else 1
    if new_string == "" and not old_string.endswith("\n") and old_string + "\n" in original:
        return original.replace(old_string + "\n", new_string, count)
    return original.replace(old_string, new_string, count)


# ── ReadFile ─────────────────────────────────────────────────────────────────

def add_line_numbers(lines: list[str], start_line: int) -> str:
    """`src/utils/file.ts` -> `addLineNumbers`, compact (`N<TAB>line`) form."""
    return "\n".join(f"{start_line + i}\t{line}" for i, line in enumerate(lines))


def build_read_file_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
    def validate_read(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
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
        except Exception as e:
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
        prompt=get_prompt("read_file.md", "Reads a file.", vars=vars),
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
        call=call_read,
    )


# ── WriteFile ────────────────────────────────────────────────────────────────

def build_write_file_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
    def validate_write(input_args: dict[str, Any], context: ToolContext) -> ValidationResult:
        ws = context.workspace
        if ws is None:
            return VALID  # call() reports the missing workspace
        path = str(input_args.get("file_path") or "")
        current = read_text(ws, path)
        if current is None:
            return VALID
        return check_read_before_write(context, path, current, not_read_code=2, modified_code=3)

    async def call_write(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = str(input_args.get("file_path") or "")
        content = str(input_args.get("content", ""))
        ws = context.workspace
        if ws is None:
            return NO_WORKSPACE

        try:
            existing = read_text(ws, file_path)
            if is_stale(context, file_path, existing):
                return ToolResult(content=FILE_UNEXPECTEDLY_MODIFIED_ERROR, is_error=True)
            ws.write_file(file_path, content)
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
        record_write(context, file_path, content)
        if existing is None:
            return ToolResult(content=f"File created successfully at: {file_path}")
        return ToolResult(content=f"The file {file_path} has been updated successfully.")

    return build_tool(
        name="WriteFile",
        prompt=get_prompt("write_file.md", "Writes a file.", vars=vars),
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
        call=call_write,
    )


# ── Edit ─────────────────────────────────────────────────────────────────────

def build_edit_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
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

        content = read_text(ws, file_path)
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

        try:
            original = read_text(ws, file_path)
            if is_stale(context, file_path, original):
                return ToolResult(content=FILE_UNEXPECTEDLY_MODIFIED_ERROR, is_error=True)
            base = original or ""
            actual_old = find_actual_string(base, old_string) or old_string
            actual_new = preserve_quote_style(old_string, actual_old, new_string)
            updated = actual_new if actual_old == "" else apply_edit_to_file(base, actual_old, actual_new, replace_all)
            ws.write_file(file_path, updated)
        except Exception as e:
            return ToolResult(content=f"Error editing file: {e}", is_error=True)

        record_write(context, file_path, updated)
        if replace_all:
            return ToolResult(
                content=f"The file {file_path} has been updated. All occurrences were successfully replaced."
            )
        return ToolResult(content=f"The file {file_path} has been updated successfully.")

    return build_tool(
        name="Edit",
        prompt=get_prompt("edit.md", "Edits a file.", vars=vars),
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
        call=call_edit,
    )


# ── Glob ─────────────────────────────────────────────────────────────────────

def extract_glob_base_directory(pattern: str) -> tuple[str, str]:
    """
    `src/utils/glob.ts` -> `extractGlobBaseDirectory`: the static directory before the first
    glob character, and the remaining pattern. rg's `--glob` only takes relative patterns.
    """
    match = re.search(r"[*?[{]", pattern)
    if match is None:
        head, _, tail = pattern.rpartition("/")
        return (head or ("/" if pattern.startswith("/") else ".")), tail
    static_prefix = pattern[:match.start()]
    last_sep = static_prefix.rfind("/")
    if last_sep == -1:
        return "", pattern
    base = static_prefix[:last_sep] or "/"
    return base, pattern[last_sep + 1:]


def build_glob_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
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

        search_dir = normalize_path(str(input_args.get("path") or "."))
        if pattern.startswith("/"):
            # Workspace paths are root-relative, so an "absolute" pattern names the root.
            base, pattern = extract_glob_base_directory(pattern)
            search_dir = normalize_path(base)
        args = ["--files", "--glob", pattern, "--sort=modified", "--no-ignore", "--hidden"]
        try:
            paths = [to_relative_path(p) for p in rip_grep(ws, args, search_dir)]
        except Exception as e:
            return ToolResult(content=f"Glob error: {e}", is_error=True)
        if not paths:
            return ToolResult(content="No files found")
        lines = paths[:GLOB_MAX_RESULTS]
        if len(paths) > GLOB_MAX_RESULTS:
            lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
        return ToolResult(content="\n".join(lines))

    return build_tool(
        name="Glob",
        prompt=get_prompt("glob.md", "Finds files.", vars=vars),
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
        call=call_glob,
    )


# ── Grep ─────────────────────────────────────────────────────────────────────

#: Prints each path's mtime in ns (0 when the stat fails), for upstream's mtime sort.
MTIME_SCRIPT = (
    "import os,sys\n"
    "for p in sys.argv[1:]:\n"
    "    try: print(os.stat(p).st_mtime_ns)\n"
    "    except OSError: print(0)"
)


def build_grep_args(input_args: dict[str, Any]) -> list[str]:
    """`GrepTool.call`: the ripgrep argv, flag for flag."""
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
    return args


def sort_by_mtime(ws: Any, paths: list[str]) -> list[str]:
    """Newest first, filename as tiebreak; a failed stat sorts as mtime 0 (`allSettled`)."""
    if not paths:
        return paths
    try:
        res = ws.execute(shlex.join(["python3", "-c", MTIME_SCRIPT, *paths]))
        mtimes = [int(x) for x in res.stdout.split()]
    except Exception:
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


def build_grep_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
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

        target = normalize_path(str(input_args.get("path") or "."))
        try:
            results = rip_grep(ws, build_grep_args(input_args), target)
        except Exception as e:
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
        prompt=get_prompt("grep.md", "Searches file contents.", vars=vars),
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
        call=call_grep,
    )
