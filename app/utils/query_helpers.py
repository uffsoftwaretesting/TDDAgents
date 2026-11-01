"""
Query helpers for headless callers.

Ported from `reference/claude-code/src/utils/queryHelpers.ts` -> `extractReadFilesFromMessages`:
the read-file cache a resumed session starts from, rebuilt from its transcript so the file tools
know what the model already saw. A full `ReadFile` result (system reminders and line numbers
stripped) and a `WriteFile` input record the content seen; an `Edit` records the file as it is on
disk now. Ranged reads are skipped, as upstream skips them.

TDDAgents' `FileState` has no timestamp (staleness is a content comparison, see
`app/utils/file_state_cache.py`), so upstream's "only with a message timestamp" condition has
nothing to guard, and a seed helps exactly when its content still equals the file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.utils.file import strip_line_number_prefix
from app.utils.file_state_cache import FileState
from app.workspace.base import WorkspaceNotFound, WorkspacePathError

FILE_READ_TOOL_NAME = "ReadFile"
FILE_WRITE_TOOL_NAME = "WriteFile"
FILE_EDIT_TOOL_NAME = "Edit"

_SYSTEM_REMINDER = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")


def extract_read_files_from_messages(messages: Sequence[Any], context: Any) -> dict[str, FileState]:
    """`extractReadFilesFromMessages`: the cache keyed as the file tools key it (`read_state_key`)."""
    from app.tools.utils import read_state_key

    reads: dict[str, str] = {}
    writes: dict[str, tuple[str, str]] = {}
    edits: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            args = call.get("args") or {}
            call_id = str(call.get("id") or "")
            name = call.get("name")
            file_path = args.get("file_path")
            if not file_path:
                continue
            if name == FILE_READ_TOOL_NAME and args.get("offset") is None and args.get("limit") is None:
                reads[call_id] = str(file_path)
            elif name == FILE_WRITE_TOOL_NAME and args.get("content"):
                writes[call_id] = (str(file_path), str(args["content"]))
            elif name == FILE_EDIT_TOOL_NAME:
                edits[call_id] = str(file_path)

    cache: dict[str, FileState] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        call_id = message.tool_call_id
        read_path = reads.get(call_id)
        if read_path is not None and isinstance(message.content, str):
            text = _SYSTEM_REMINDER.sub("", message.content)
            content = "\n".join(strip_line_number_prefix(line) for line in text.split("\n")).strip()
            cache[read_state_key(context, read_path)] = FileState(content=content)
        write = writes.get(call_id)
        if write is not None:
            cache[read_state_key(context, write[0])] = FileState(content=write[1])
        edit_path = edits.get(call_id)
        if edit_path is not None and message.status != "error":
            try:
                disk = context.workspace.read_file(edit_path)
            except (WorkspaceNotFound, WorkspacePathError):  # `isFsInaccessible`: gone since the Edit
                continue
            cache[read_state_key(context, edit_path)] = FileState(content=disk)
    return cache


__all__ = [
    "FILE_EDIT_TOOL_NAME",
    "FILE_READ_TOOL_NAME",
    "FILE_WRITE_TOOL_NAME",
    "extract_read_files_from_messages",
]
