"""
The nine filesystem tools: ReadFile, ListDir, Glob, Grep, WriteFile, Edit, MultiEdit,
Delete, Move.

Every one is written against the `Workspace` protocol, never against `E2BAdapter`, so the
same tool works unchanged whether the agent is pointed at the sandbox or the host. Three
of them are not SDK wrappers at all, because the E2B SDK has no equivalent primitive:

* `Grep` and `Glob` shell out through `workspace.execute` — the SDK has no content search.
* `Edit` and `MultiEdit` are read-modify-write round trips, because `files.write` is
  full-overwrite only. That is what makes the sync engine's conflict rule load-bearing
  rather than theoretical: an edit is not atomic against a change on the other side.

`ReadFile` is the one tool with an unbounded `max_result_chars`. It self-bounds with
`offset`/`limit`, and persisting its output would create a ReadFile -> file -> ReadFile
loop where the preview points at a file whose contents are the preview.
"""

from __future__ import annotations

import shlex

from pydantic import BaseModel, Field

from app.tools.base import (
    AnyTool,
    GREP_MAX_RESULT_CHARS,
    UNBOUNDED_RESULT_CHARS,
    Capability,
    ToolContext,
    ToolResult,
    ValidationResult,
    build_tool,
    err,
    ok,
)
from app.workspace.base import WorkspaceNotFound, normalize_path

#: Matches claude-code's Read default. A file longer than this is paged, not truncated:
#: the tool tells the agent how to ask for the next window.
DEFAULT_READ_LINES = 2000

#: Longer lines are cut with a marker rather than allowed to blow the context window.
MAX_LINE_CHARS = 2000

GREP_DEFAULT_MAX_MATCHES = 200
GLOB_DEFAULT_MAX_RESULTS = 200


# ─────────────────────────────────────────────────────────────────────────────
# ReadFile
# ─────────────────────────────────────────────────────────────────────────────

class ReadFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative path of the file to read.")
    offset: int = Field(default=0, description="First line to return, 0-based.")
    limit: int = Field(
        default=DEFAULT_READ_LINES,
        description=f"How many lines to return. Defaults to {DEFAULT_READ_LINES}.",
    )


READ_FILE_PROMPT = f"""Reads a UTF-8 text file from the workspace.

Output is line-numbered in `cat -n` style: a right-aligned line number, a tab, then the
line's exact text. Line numbers are a reading aid — they are NOT part of the file. When
you pass text to Edit, use the raw line content with the number and tab removed.

Reads at most {DEFAULT_READ_LINES} lines at a time. If the file is longer, the result says
so and tells you the offset to use for the next window. Lines longer than {MAX_LINE_CHARS}
characters are cut with a marker.

Prefer reading a file before editing it: Edit matches on exact text and will fail if you
guess at content you have not seen."""


def _read_file(args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
    try:
        content = ctx.workspace.read_file(args.path)
    except WorkspaceNotFound:
        return err(f"File not found: {args.path}")

    if content == "":
        return ok(f"{args.path} exists but is empty.")

    lines = content.splitlines()
    start = max(args.offset, 0)
    window = lines[start:start + max(args.limit, 0)]

    if not window:
        return ok(f"{args.path} has {len(lines)} lines; offset {start} is past the end.")

    width = len(str(start + len(window)))
    rendered = "\n".join(
        f"{start + i + 1:>{width}}\t{_clip(line)}" for i, line in enumerate(window)
    )

    remaining = len(lines) - (start + len(window))
    if remaining > 0:
        rendered += (
            f"\n\n... {remaining} more line(s). "
            f"Read the next window with offset={start + len(window)}."
        )
    return ok(rendered)


def _clip(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS] + f"... [line cut at {MAX_LINE_CHARS} characters]"


ReadFile = build_tool(
    name="ReadFile",
    args_schema=ReadFileArgs,
    prompt=READ_FILE_PROMPT,
    call=_read_file,
    description=lambda args: f"Read {args.path}",
    max_result_chars=UNBOUNDED_RESULT_CHARS,
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


# ─────────────────────────────────────────────────────────────────────────────
# ListDir
# ─────────────────────────────────────────────────────────────────────────────

class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Workspace-relative directory. '.' is the root.")
    depth: int = Field(default=1, description="How many levels to descend. 1 is immediate children.")


def _list_dir(args: ListDirArgs, ctx: ToolContext) -> ToolResult:
    try:
        entries = ctx.workspace.list_files(args.path, depth=args.depth)
    except WorkspaceNotFound:
        return err(f"Directory not found: {args.path}")

    if not entries:
        return ok(f"{args.path} is empty.")

    rendered = "\n".join(
        f"{'d' if e.is_dir else '-'} {e.path}" + ("" if e.is_dir else f"  ({e.size} bytes)")
        for e in entries
    )
    return ok(rendered)


ListDir = build_tool(
    name="ListDir",
    args_schema=ListDirArgs,
    prompt=(
        "Lists the entries under a workspace directory. Each line is 'd' or '-' for "
        "directory or file, then the workspace-relative path. Use depth to descend "
        "further; depth=1 lists only immediate children."
    ),
    call=_list_dir,
    description=lambda args: f"List {args.path}",
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


# ─────────────────────────────────────────────────────────────────────────────
# Glob
# ─────────────────────────────────────────────────────────────────────────────

class GlobArgs(BaseModel):
    pattern: str = Field(description="A shell glob, e.g. '**/*.py' or 'tests/test_*.py'.")
    path: str = Field(default=".", description="Directory to search under.")


def _glob(args: GlobArgs, ctx: ToolContext) -> ToolResult:
    # `find -path` rather than -name, so a pattern containing a slash means what it says.
    pattern = args.pattern if args.pattern.startswith(("*", "/")) else f"*{args.pattern}"
    command = (
        f"find {shlex.quote(normalize_path(args.path))} -type f "
        f"-path {shlex.quote(pattern)} 2>/dev/null | head -n {GLOB_DEFAULT_MAX_RESULTS}"
    )
    result = ctx.workspace.execute(command)

    matches = [line for line in result.stdout.splitlines() if line.strip()]
    if not matches:
        return ok(f"No files match {args.pattern} under {args.path}.")
    return ok("\n".join(matches))


Glob = build_tool(
    name="Glob",
    args_schema=GlobArgs,
    prompt=(
        "Finds files by path pattern. Returns matching workspace-relative paths, one per "
        f"line, capped at {GLOB_DEFAULT_MAX_RESULTS}. Use this when you know something "
        "about a file's name or location; use Grep when you know something about its "
        "contents."
    ),
    call=_glob,
    description=lambda args: f"Glob {args.pattern}",
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


# ─────────────────────────────────────────────────────────────────────────────
# Grep
# ─────────────────────────────────────────────────────────────────────────────

class GrepArgs(BaseModel):
    pattern: str = Field(description="An extended regular expression to search for.")
    path: str = Field(default=".", description="File or directory to search.")
    glob: str = Field(default="", description="Optional filename filter, e.g. '*.py'.")
    case_insensitive: bool = Field(default=False, description="Ignore case.")


def _grep(args: GrepArgs, ctx: ToolContext) -> ToolResult:
    flags = "-rnE"
    if args.case_insensitive:
        flags += "i"

    include = f"--include={shlex.quote(args.glob)} " if args.glob else ""
    command = (
        f"grep {flags} {include}-- {shlex.quote(args.pattern)} "
        f"{shlex.quote(normalize_path(args.path))} 2>/dev/null "
        f"| head -n {GREP_DEFAULT_MAX_MATCHES}"
    )
    result = ctx.workspace.execute(command)

    matches = [line for line in result.stdout.splitlines() if line.strip()]
    if not matches:
        return ok(f"No matches for /{args.pattern}/ under {args.path}.")

    body = "\n".join(matches)
    if len(matches) >= GREP_DEFAULT_MAX_MATCHES:
        body += f"\n\n[stopped at {GREP_DEFAULT_MAX_MATCHES} matches; narrow the pattern]"
    return ok(body)


Grep = build_tool(
    name="Grep",
    args_schema=GrepArgs,
    prompt=(
        "Searches file contents with an extended regular expression. Returns "
        "'path:line:text' for each match, capped at "
        f"{GREP_DEFAULT_MAX_MATCHES}. Narrow with `glob` to restrict which files are "
        "searched. Use this when you know something about the contents; use Glob when "
        "you know something about the name."
    ),
    call=_grep,
    description=lambda args: f"Grep /{args.pattern}/",
    max_result_chars=GREP_MAX_RESULT_CHARS,
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
)


# ─────────────────────────────────────────────────────────────────────────────
# WriteFile
# ─────────────────────────────────────────────────────────────────────────────

class WriteFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to write.")
    content: str = Field(description="The complete new contents of the file.")


def _write_file(args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
    existed = ctx.workspace.exists(args.path)
    ctx.workspace.write_file(args.path, args.content)
    verb = "Overwrote" if existed else "Created"
    lines = len(args.content.splitlines())
    return ok(f"{verb} {args.path} ({lines} line(s), {len(args.content)} chars).")


WriteFile = build_tool(
    name="WriteFile",
    args_schema=WriteFileArgs,
    prompt=(
        "Writes a file, creating parent directories as needed and replacing the file "
        "wholesale if it already exists. Pass the complete intended contents — this is "
        "not a patch. To change part of an existing file, use Edit instead, which will "
        "not silently discard the parts you did not mention."
    ),
    call=_write_file,
    description=lambda args: f"Write {args.path}",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.WRITE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Edit and MultiEdit
# ─────────────────────────────────────────────────────────────────────────────

class EditArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to edit.")
    old_string: str = Field(description="Exact text to replace. Must appear in the file.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(
        default=False, description="Replace every occurrence instead of requiring a unique one."
    )


EDIT_PROMPT = """Replaces exact text inside an existing file.

`old_string` must match the file's raw content character for character, including
indentation. It must NOT include the line numbers or tab that ReadFile adds for display —
strip those first.

By default `old_string` must appear exactly once, so an ambiguous edit fails loudly rather
than changing the wrong line. Include surrounding context to make it unique, or set
replace_all to change every occurrence."""


def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> tuple[str, str | None]:
    """Returns (new_content, error). Error is None on success."""
    if old == new:
        return content, "old_string and new_string are identical; nothing to do."

    occurrences = content.count(old)
    if occurrences == 0:
        return content, "old_string was not found in the file."
    if occurrences > 1 and not replace_all:
        return content, (
            f"old_string appears {occurrences} times; it must be unique. Add surrounding "
            f"context to disambiguate, or set replace_all=true."
        )

    return (content.replace(old, new) if replace_all else content.replace(old, new, 1)), None


def _edit(args: EditArgs, ctx: ToolContext) -> ToolResult:
    try:
        content = ctx.workspace.read_file(args.path)
    except WorkspaceNotFound:
        return err(f"File not found: {args.path}. Use WriteFile to create it.")

    updated, error = _apply_edit(content, args.old_string, args.new_string, args.replace_all)
    if error is not None:
        return err(f"{error} (file: {args.path})")

    ctx.workspace.write_file(args.path, updated)
    replaced = content.count(args.old_string) if args.replace_all else 1
    return ok(f"Edited {args.path} ({replaced} replacement(s)).")


Edit = build_tool(
    name="Edit",
    args_schema=EditArgs,
    prompt=EDIT_PROMPT,
    call=_edit,
    description=lambda args: f"Edit {args.path}",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.WRITE,
)


class EditOperation(BaseModel):
    old_string: str = Field(description="Exact text to replace.")
    new_string: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


class MultiEditArgs(BaseModel):
    path: str = Field(description="Workspace-relative path to edit.")
    edits: list[EditOperation] = Field(description="Edits to apply in order.")


def _multi_edit(args: MultiEditArgs, ctx: ToolContext) -> ToolResult:
    try:
        content = ctx.workspace.read_file(args.path)
    except WorkspaceNotFound:
        return err(f"File not found: {args.path}. Use WriteFile to create it.")

    working = content
    for position, edit in enumerate(args.edits, start=1):
        working, error = _apply_edit(working, edit.old_string, edit.new_string, edit.replace_all)
        if error is not None:
            # All or nothing: a half-applied batch would leave the file in a state the
            # agent never asked for and cannot reason about from the error alone.
            return err(
                f"Edit {position} of {len(args.edits)} failed: {error} " f"No changes were written to {args.path}.",
            )

    ctx.workspace.write_file(args.path, working)
    return ok(f"Applied {len(args.edits)} edit(s) to {args.path}.")


MultiEdit = build_tool(
    name="MultiEdit",
    args_schema=MultiEditArgs,
    prompt=(
        "Applies several Edit operations to one file in a single call. Each edit follows "
        "Edit's rules and they are applied in order, so a later edit sees the result of "
        "an earlier one.\n\n"
        "All or nothing: if any edit fails, the file is left completely untouched and the "
        "error names which edit failed. Prefer this over several Edit calls on the same "
        "file — it cannot leave the file half-changed."
    ),
    call=_multi_edit,
    description=lambda args: f"MultiEdit {args.path} ({len(args.edits)} edits)",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.WRITE,
    validate_input=lambda args, ctx: (
        ValidationResult() if args.edits else ValidationResult.invalid("edits must not be empty.")
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Delete and Move
# ─────────────────────────────────────────────────────────────────────────────

class DeleteArgs(BaseModel):
    path: str = Field(description="Workspace-relative file or directory to remove.")


def _delete(args: DeleteArgs, ctx: ToolContext) -> ToolResult:
    try:
        ctx.workspace.delete_file(args.path)
    except WorkspaceNotFound:
        return err(f"Nothing to delete at {args.path}.")
    return ok(f"Deleted {args.path}.")


Delete = build_tool(
    name="Delete",
    args_schema=DeleteArgs,
    prompt="Removes a file or directory from the workspace. This cannot be undone.",
    call=_delete,
    description=lambda args: f"Delete {args.path}",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.WRITE,
)


class MoveArgs(BaseModel):
    old: str = Field(description="Current workspace-relative path.")
    new: str = Field(description="New workspace-relative path.")


def _move(args: MoveArgs, ctx: ToolContext) -> ToolResult:
    try:
        ctx.workspace.move(args.old, args.new)
    except WorkspaceNotFound:
        return err(f"Nothing to move at {args.old}.")
    return ok(f"Moved {args.old} to {args.new}.")


Move = build_tool(
    name="Move",
    args_schema=MoveArgs,
    prompt=(
        "Renames or moves a file or directory, creating the destination's parent "
        "directories as needed. An existing file at the destination is replaced."
    ),
    call=_move,
    description=lambda args: f"Move {args.old} -> {args.new}",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.WRITE,
)


FS_TOOLS: list[AnyTool] = [ReadFile, ListDir, Glob, Grep, WriteFile, Edit, MultiEdit, Delete, Move]
