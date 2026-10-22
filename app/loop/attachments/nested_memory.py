"""
The `nested_memory` source: instructions that apply to a file the agent just read.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `getNestedMemoryAttachments`,
`getNestedMemoryAttachmentsForFile`, `getDirectoriesToProcess`, `memoryFilesToAttachments`, and
`src/utils/claudemd.ts` -> `getManagedAndUserConditionalRules`,
`getMemoryFilesForNestedDirectory`, `getConditionalRulesForCwdLevelDirectory`,
`processConditionedMdRules`.

When `ReadFile` reads a file (upstream adds it to `nestedMemoryAttachmentTriggers`), the next
attachment pass attaches, for that file: managed and user conditional rules whose `paths:`
match it; then, for each directory between the workspace root and the file, that directory's
`TDDAGENTS.md`, `.tddagents/TDDAGENTS.md`, `TDDAGENTS.local.md` and `.tddagents/rules/*.md`
(unconditional, then conditional ones that match); then the root's conditional rules. A file
already attached this run (`loadedNestedMemoryPaths`) or already read is not attached again,
and an attached one is recorded in `read_file_state` as if read.

Paths are workspace-relative: the workspace root is upstream's original cwd, so the
"cwd-level" directories are the root alone (memory above the workspace is host memory — user
and managed — which `get_memory_files` already loads).
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pathspec

from app.loop.attachments.types import Attachment
from app.loop.context.file_state import FileState
from app.loop.context.memory import (
    CONFIG_DIR_NAME,
    LOCAL_MEMORY_FILE_NAME,
    MANAGED_DIR,
    MEMORY_FILE_NAME,
    HostSource,
    MemoryFileInfo,
    MemorySource,
    WorkspaceSource,
    process_md_rules,
    process_memory_file,
)
from app.loop.messages import Message
from app.workspace.base import WorkspaceError, normalize_path

RULES_SUBDIR = f"{CONFIG_DIR_NAME}/rules"


@dataclass
class NestedMemoryState:
    """`nestedMemoryAttachmentTriggers` and `loadedNestedMemoryPaths`, both mutated in place."""

    triggers: set[str] = field(default_factory=set)
    loaded_paths: set[str] = field(default_factory=set)


def get_directories_to_process(target_path: str) -> tuple[list[str], list[str]]:
    """`getDirectoriesToProcess`, workspace-relative: (nested dirs root-first, cwd-level dirs)."""
    parent = posixpath.dirname(target_path)
    nested: list[str] = []
    while parent not in ("", "."):
        nested.append(parent)
        parent = posixpath.dirname(parent)
    nested.reverse()
    return nested, [""]


def _matches(globs: tuple[str, ...] | None, target_path: str, base_dir: str) -> bool:
    if not globs:
        return False
    if base_dir:
        if not target_path.startswith(base_dir + "/"):
            return False
        relative = target_path[len(base_dir) + 1:]
    else:
        relative = target_path
    if not relative or relative.startswith(".."):
        return False
    return pathspec.GitIgnoreSpec.from_lines(globs).match_file(relative)


def process_conditioned_md_rules(
    source: MemorySource,
    target_path: str,
    rules_dir: str,
    type: Any,
    processed: set[str],
    include_external: bool,
    base_dir: str,
) -> list[MemoryFileInfo]:
    """`processConditionedMdRules`: conditional rules whose globs match the target."""
    rules = process_md_rules(source, rules_dir, type, processed, include_external, conditional_rule=True)
    return [rule for rule in rules if _matches(rule.globs, target_path, base_dir)]


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _nested_directory_files(
    workspace: WorkspaceSource, directory: str, target_path: str, processed: set[str]
) -> list[MemoryFileInfo]:
    """`getMemoryFilesForNestedDirectory`."""
    result: list[MemoryFileInfo] = []
    result += process_memory_file(workspace, _join(directory, MEMORY_FILE_NAME), "Project", processed, False)
    result += process_memory_file(
        workspace, _join(directory, f"{CONFIG_DIR_NAME}/{MEMORY_FILE_NAME}"), "Project", processed, False
    )
    result += process_memory_file(workspace, _join(directory, LOCAL_MEMORY_FILE_NAME), "Local", processed, False)
    rules_dir = _join(directory, RULES_SUBDIR)
    unconditional_processed = set(processed)
    result += process_md_rules(workspace, rules_dir, "Project", unconditional_processed, False)
    result += process_conditioned_md_rules(workspace, target_path, rules_dir, "Project", processed, False, directory)
    processed |= unconditional_processed
    return result


def _to_attachments(files: list[MemoryFileInfo], context: Any, state: NestedMemoryState) -> list[Attachment]:
    """`memoryFilesToAttachments`."""
    out: list[Attachment] = []
    read_state: dict[str, FileState] = context.read_file_state
    for info in files:
        if info.path in state.loaded_paths or info.path in read_state:
            continue
        out.append(Attachment("nested_memory", {"path": info.path, "content": info.content, "displayPath": info.path}))
        state.loaded_paths.add(info.path)
        differs = info.content_differs_from_disk
        read_state[info.path] = FileState(
            content=(info.raw_content or info.content) if differs else info.content, is_partial_view=differs
        )
    return out


async def get_nested_memory_attachments(
    context: Any,
    state: NestedMemoryState,
    *,
    home: str | None = None,
    managed_dir: str = MANAGED_DIR,
) -> list[Attachment]:
    """`getNestedMemoryAttachments`: one pass over the pending triggers, which it clears."""
    workspace = getattr(context, "workspace", None)
    if not state.triggers:
        return []
    triggers = sorted(state.triggers)
    state.triggers.clear()
    if workspace is None:
        return []
    ws_source = WorkspaceSource(workspace)
    host = HostSource()
    user_rules = os.path.join(home if home is not None else os.path.expanduser("~"), CONFIG_DIR_NAME, "rules")
    managed_rules = os.path.join(managed_dir, CONFIG_DIR_NAME, "rules")
    out: list[Attachment] = []
    for raw in triggers:
        try:
            target = normalize_path(raw)
        except WorkspaceError:
            continue
        processed: set[str] = set()
        files = process_conditioned_md_rules(host, target, managed_rules, "Managed", processed, False, "")
        files += process_conditioned_md_rules(host, target, user_rules, "User", processed, True, "")
        out += _to_attachments(files, context, state)
        nested, cwd_level = get_directories_to_process(target)
        for directory in nested:
            out += _to_attachments(_nested_directory_files(ws_source, directory, target, processed), context, state)
        for directory in cwd_level:
            rules = process_conditioned_md_rules(
                ws_source, target, _join(directory, RULES_SUBDIR), "Project", processed, False, directory
            )
            out += _to_attachments(rules, context, state)
    return out


def nested_memory_state_of(context: Any) -> NestedMemoryState | None:
    """
    The context's `nested_memory_attachment_triggers` / `loaded_nested_memory_paths`, as a
    live view; `None` when the context carries no triggers (nothing has asked for them).
    """
    triggers = getattr(context, "nested_memory_attachment_triggers", None)
    if not isinstance(triggers, set):
        return None
    loaded = getattr(context, "loaded_nested_memory_paths", None)
    return NestedMemoryState(triggers=triggers, loaded_paths=loaded if isinstance(loaded, set) else set())


async def get_nested_memory_source(messages: Sequence[Message], context: Any) -> list[Attachment]:
    """The pipeline's `nested_memory` entry."""
    state = nested_memory_state_of(context)
    if state is None:
        return []
    return await get_nested_memory_attachments(context, state)


__all__ = [
    "NestedMemoryState",
    "get_directories_to_process",
    "get_nested_memory_attachments",
    "get_nested_memory_source",
    "nested_memory_state_of",
    "process_conditioned_md_rules",
]
