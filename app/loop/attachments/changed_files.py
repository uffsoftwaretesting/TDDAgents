"""
The `changed_files` source: files the agent read that changed under it.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getChangedFiles` and
`src/tools/FileEditTool/utils.ts` -> `getSnippetForTwoFileDiff` (8 lines of context,
`DIFF_SNIPPET_MAX_BYTES`).

For every file in `read_file_state` that was read whole: if it is gone, it is forgotten; if it
differs from what was read, the new content is recorded (upstream re-reads it through
`FileReadTool.call`, which updates the cache) and an `edited_text_file` attachment carries
the changed hunks with line numbers. *Divergence:* the `Workspace` protocol exposes no mtime
(see `context/file_state.py`), so "changed" is decided by content, the fallback upstream's own
read-before-write check uses. A Read-denied path is skipped, as upstream skips it.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any

from app.loop.attachments.types import Attachment
from app.loop.context.file_state import FileState
from app.loop.messages import Message
from app.workspace.base import WorkspaceError, WorkspaceNotFound

#: `DIFF_SNIPPET_MAX_BYTES`.
DIFF_SNIPPET_MAX_BYTES = 8192
#: `structuredPatch(..., { context: 8 })` in `getSnippetForTwoFileDiff`.
SNIPPET_CONTEXT_LINES = 8


def _number(lines: list[str], start_line: int) -> str:
    """`addLineNumbers` with the compact `N\\t` prefix this build's ReadFile uses."""
    if not lines:
        return ""
    return "\n".join(f"{start_line + i}\t{line}" for i, line in enumerate(lines))


def get_snippet_for_two_file_diff(before: str, after: str) -> str:
    """`getSnippetForTwoFileDiff`: each hunk's surviving lines, numbered from the old start."""
    a = before.splitlines()
    b = after.splitlines()
    hunks: list[str] = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(SNIPPET_CONTEXT_LINES):
        start = group[0][1] + 1
        kept: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                kept.extend(a[i1:i2])
            elif tag in ("replace", "insert"):
                kept.extend(b[j1:j2])
        hunks.append(_number(kept, start))
    if not hunks:
        return ""
    full = "\n...\n".join(hunks)
    if len(full) <= DIFF_SNIPPET_MAX_BYTES:
        return full
    # `lastIndexOf('\n', DIFF_SNIPPET_MAX_BYTES + 1)` searches up to and including that index.
    cutoff = full.rfind("\n", 0, DIFF_SNIPPET_MAX_BYTES + 1)
    kept_text = full[:cutoff] if cutoff > 0 else full[:DIFF_SNIPPET_MAX_BYTES]
    remaining = full.count("\n", len(kept_text)) + 1
    return f"{kept_text}\n\n... [{remaining} lines truncated] ..."


def _read_denied(path: str, context: Any) -> bool:
    try:
        from app.loop.permissions.filesystem import (
            matching_rule_for_input,
            permission_cwd,
        )
        from app.loop.permissions.rules import extract_permission_context

        perm = extract_permission_context(context)
        return matching_rule_for_input(path, perm, "read", "deny", permission_cwd(context)) is not None
    except Exception:  # noqa: BLE001 - a context without permissions denies nothing
        return False


def get_changed_files(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """`getChangedFiles`."""
    workspace = getattr(context, "workspace", None)
    state: dict[str, FileState] = getattr(context, "read_file_state", {})
    if workspace is None or not state:
        return []
    out: list[Attachment] = []
    for path, seen in list(state.items()):
        if seen.offset is not None or seen.limit is not None:
            continue
        if _read_denied(path, context):
            continue
        try:
            current = workspace.read_file(path)
        except WorkspaceNotFound:
            state.pop(path, None)
            continue
        except (WorkspaceError, OSError, UnicodeDecodeError):
            continue
        if current == seen.content:
            continue
        state[path] = FileState(content=current)
        snippet = get_snippet_for_two_file_diff(seen.content, current)
        if snippet == "":
            continue
        out.append(Attachment("edited_text_file", {"filename": path, "snippet": snippet}))
    return out


__all__ = ["DIFF_SNIPPET_MAX_BYTES", "get_changed_files", "get_snippet_for_two_file_diff"]
