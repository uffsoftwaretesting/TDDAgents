"""
What the five file tools share: `ReadFile` (`file_read_tool/`), `WriteFile`
(`file_write_tool/`), `Edit` (`file_edit_tool/`), `Glob` (`glob_tool/`), `Grep` (`grep_tool/`).

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
call). Read-before-write uses `ToolContext.read_file_state` (`app/utils/file_state_cache.py`).

`ReadFile`, `Glob` and `Grep` check permissions with upstream's `checkReadPermissionForTool`:
`Read(...)` deny/ask rules, reads inside the workspace allowed, this project's config
directory (persisted tool results) readable, anything else an 'ask'. `Glob` and `Grep` also
pass the `Read` deny patterns to ripgrep as exclude globs, as upstream does, so a denied file
under a searchable directory is hidden rather than leaked. `ReadFile` refuses a Read-denied
path in `validate_input` (errorCode 1), before any I/O.

`WriteFile` and `Edit` check permissions with upstream's `checkWritePermissionForTool`
(`app/utils/permissions/filesystem.py`): `Edit(...)` path rules, the dangerous-path safety
check, and `acceptEdits` inside the workspace. In `default` mode a write is an 'ask', which a
headless run denies — the run's mode comes from settings `defaultMode` or the run config, as
upstream's comes from `--permission-mode`.

Every operation goes through the `Workspace` protocol, so the same tool works against the
sandbox and the host, and the workspace's path containment is the boundary.

`WriteFile` and `Edit` are *generic* writers (`GENERIC_WRITER_TOOL_NAMES` in
`app/tdd/phase_permissions.py`): the TDD gate classifies each call by its target path. They
must not claim `is_implementation_writer` / `is_test_writer`, because pool assembly strips
tools carrying those flags wholesale — which would remove every write primitive.

Glob and Grep run ripgrep inside the workspace exactly as upstream does
(`app/utils/ripgrep.py`), with the same argv.

Divergences, each forced by the `Workspace` protocol:

* Staleness is decided by content comparison, not timestamps (see `utils/file_state_cache.py`).
* Paths are workspace-relative; an absolute Glob pattern is re-rooted at the workspace.
* The plugin-cache exclusions are not passed to rg: there is no plugin cache here.
* Read-deny exclude globs are anchored at the workspace root (where rg runs) for Glob too;
  upstream anchors Glob's at the search directory, which is rg's cwd there.
* Read omits images, PDFs (`pages`) and notebooks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.tool import ToolContext, ToolResult, ValidationResult
from app.utils.file_state_cache import FileState
from app.utils.frontmatter_parser import parse_markdown_frontmatter
from app.utils.permissions.shell_rule_matching import match_wildcard_pattern
from app.utils.tool_result_storage import TOOL_RESULTS_SUBDIR, config_home
from app.workspace.base import WorkspaceNotFound, workspace_path

FILE_NOT_READ_ERROR = "File has not been read yet. Read it first before writing to it."
FILE_MODIFIED_SINCE_READ_ERROR = (
    "File has been modified since read, either by the user or by a linter. "
    "Read it again before attempting to write it."
)


NO_WORKSPACE = ToolResult(content="No workspace available", is_error=True)
VALID = ValidationResult(valid=True)


def get_prompt(path: Path, fallback: str) -> str:
    """The body of a tool's `prompt.md` (frontmatter dropped), or `fallback` if it is missing."""
    if path.exists():
        return parse_markdown_frontmatter(path.read_text(encoding="utf-8"))[1]
    return fallback


def invalid(message: str, code: int) -> ValidationResult:
    return ValidationResult(valid=False, message=message, error_code=code)


# ── shared helpers ───────────────────────────────────────────────────────────


def host_path(context: ToolContext | None, path: str) -> Path | None:
    """
    The host file a path names when it lives outside the workspace on purpose: a persisted tool
    result, or this run's agent memory (`agent_memory_target`). `None` for a workspace path.
    """
    if is_persisted_result_path(path):
        return Path(path).expanduser()
    from app.tools.agent_tool.agent_memory import agent_memory_target

    memory = agent_memory_target(context, path) if context is not None else None
    return Path(memory) if memory is not None else None


def read_text(ws: Any, path: str, context: ToolContext | None = None) -> str | None:
    """
    File content with CRLF normalized (as `readFileForEdit` does), or None if missing.

    A persisted tool result or agent memory is read straight off the host (`host_path`): it
    lives outside the workspace on purpose, so the workspace cannot resolve it.
    """
    host = host_path(context, path)
    if host is not None:
        try:
            return host.read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError:
            return None
    try:
        content: str = ws.read_file(path)
    except WorkspaceNotFound:
        return None
    return content.replace("\r\n", "\n")


def read_state_key(context: ToolContext, path: str) -> str:
    """
    The `readFileState` key of `path`: its workspace-relative form, or the host path itself for
    a persisted tool result, which lives outside the workspace on purpose.
    """
    host = host_path(context, path)
    if host is not None:
        return str(host)
    return workspace_path(context.workspace, path)


def write_text(ws: Any, path: str, content: str, context: ToolContext) -> None:
    """Write through the workspace, or on the host for this run's agent memory (`host_path`)."""
    host = host_path(context, path)
    if host is None:
        ws.write_file(path, content)
        return
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_text(content, encoding="utf-8")


def check_read_before_write(
    context: ToolContext, path: str, current: str, not_read_code: int, modified_code: int
) -> ValidationResult:
    """The `readFileState` precondition shared by `FileWriteTool` and `FileEditTool`."""
    state = context.read_file_state.get(read_state_key(context, path))
    if state is None or state.is_partial_view:
        return invalid(FILE_NOT_READ_ERROR, not_read_code)
    if state.content != current:
        return invalid(FILE_MODIFIED_SINCE_READ_ERROR, modified_code)
    return VALID


def is_stale(context: ToolContext, path: str, current: str | None) -> bool:
    """Call-time recheck: upstream re-validates between validation and the write."""
    if current is None:
        return False
    state = context.read_file_state.get(read_state_key(context, path))
    return state is None or state.content != current


def activate_conditional_skills(context: ToolContext, file_path: str) -> None:
    """
    `activateConditionalSkillsForPaths([file], cwd)`, as FileRead/FileWrite/FileEdit call it: a
    skill whose `paths` match the file becomes invocable. Paths here are workspace-relative,
    and the workspace is the run's working directory, so the path is matched as is.
    """
    registry = context.skill_registry
    if registry is not None and host_path(context, file_path) is None:
        registry.activate_conditional_skills_for_paths([workspace_path(context.workspace, file_path)], "")


def record_write(context: ToolContext, path: str, content: str) -> None:
    """Edit/Write store `offset=undefined`, so the next edit needs no fresh Read."""
    context.read_file_state[read_state_key(context, path)] = FileState(content=content)


def path_kind(ws: Any, path: str) -> str | None:
    """'dir', 'file', or None if absent — decided through the protocol alone."""
    normalized = workspace_path(ws, path or ".")
    if normalized == ".":
        return "dir"
    if not ws.exists(normalized):
        return None
    parent = normalized.rsplit("/", 1)[0] if "/" in normalized else "."
    for entry in ws.list_files(parent, depth=1):
        if entry.path == normalized:
            return "dir" if entry.is_dir else "file"
    return "file"


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# ── Edit helpers (FileEditTool/utils.ts) ─────────────────────────────────────


# ── ReadFile ─────────────────────────────────────────────────────────────────


def file_path_permission_matcher(args: Mapping[str, Any]) -> Callable[[str], bool]:
    """
    `preparePermissionMatcher` of `Read`/`Write`/`Edit`: a rule pattern against `file_path`.
    *Divergence:* upstream matches the path after `backfillObservableInput` made it
    absolute; this pipeline has no backfill, so the path is matched as the model wrote it.
    """
    path = str(args.get("file_path", ""))
    return lambda pattern: match_wildcard_pattern(pattern, path)


def search_pattern_permission_matcher(args: Mapping[str, Any]) -> Callable[[str], bool]:
    """`preparePermissionMatcher` of `Glob`/`Grep`: a rule pattern against the search `pattern`."""
    searched = str(args.get("pattern", ""))
    return lambda rule: match_wildcard_pattern(rule, searched)


# ── WriteFile ────────────────────────────────────────────────────────────────


# ── Edit ─────────────────────────────────────────────────────────────────────


# ── Glob ─────────────────────────────────────────────────────────────────────


# ── Grep ─────────────────────────────────────────────────────────────────────


def is_persisted_result_path(path: str) -> bool:
    """
    Whether `path` names a file in this run's persisted-tool-result store.

    That store is the one place outside the workspace `ReadFile` may look. Upstream has no
    equivalent check because its `Read` is not workspace-bounded at all; ours is, and
    without this the path handed to the model by an oversized result would be a path the
    model is then refused — a preview advertising a file it cannot open.
    """
    try:
        candidate = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    root = (config_home() / "projects").resolve()
    return root in candidate.parents and candidate.parent.name == TOOL_RESULTS_SUBDIR
