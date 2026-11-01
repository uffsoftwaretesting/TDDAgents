"""
Surfacing relevant memories as an attachment, prefetched while the turn runs.

Ported from `reference/claude-code/src/utils/attachments.ts` -> `MAX_MEMORY_LINES`,
`MAX_MEMORY_BYTES`, `RELEVANT_MEMORIES_CONFIG`, `getRelevantMemoryAttachments`,
`collectSurfacedMemories`, `readMemoriesForSurfacing`, `memoryHeader`, `startRelevantMemoryPrefetch`,
`collectRecentSuccessfulTools`, `filterDuplicateMemoryAttachments`. Kept beside memdir rather
than in `app/utils/attachments.py` so that module does not import the memory stack.

On only with auto memory and the `TDDAGENTS_MEMORY_RELEVANCE` opt-in (upstream's
`tengu_moth_copse`, default off). Not ported: agent-mention memory directories (`@agent-x`), the
prefetch's abort on an early turn end (the selection is simply discarded) and the telemetry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.memdir.find_relevant_memories import (
    RelevantMemory,
    Select,
    find_relevant_memories,
)
from app.memdir.memdir import is_relevance_search_enabled
from app.memdir.memory_age import memory_age, memory_freshness_text
from app.utils.attachments import Attachment, attachment_of
from app.utils.file_state_cache import FileState

#: `MAX_MEMORY_LINES` / `MAX_MEMORY_BYTES`: one surfaced memory's cap.
MAX_MEMORY_LINES = 200
MAX_MEMORY_BYTES = 4096
#: `RELEVANT_MEMORIES_CONFIG.MAX_SESSION_BYTES`: past this, no more prefetching.
MAX_SESSION_BYTES = 60 * 1024
#: `getRelevantMemoryAttachments`: at most this many memories per turn.
MAX_SELECTED = 5
FILE_READ_TOOL_NAME = "ReadFile"


def memory_header(path: str, mtime_ms: float, now_ms: float | None = None) -> str:
    """`memoryHeader`: the staleness caveat for an old memory, else when it was saved."""
    staleness = memory_freshness_text(mtime_ms, now_ms)
    if staleness:
        return f"{staleness}\n\nMemory: {path}:"
    return f"Memory (saved {memory_age(mtime_ms, now_ms)}): {path}:"


def collect_surfaced_memories(messages: Sequence[Any]) -> tuple[frozenset[str], int]:
    """`collectSurfacedMemories`: the paths already surfaced and their total bytes (compaction resets both)."""
    paths: set[str] = set()
    total = 0
    for message in messages:
        attachment = attachment_of(message)
        if attachment is not None and attachment.get("type") == "relevant_memories":
            for memory in attachment.get("memories", ()):
                paths.add(str(memory["path"]))
                total += len(str(memory["content"]))
    return frozenset(paths), total


def read_memories_for_surfacing(selected: Sequence[RelevantMemory]) -> list[dict[str, Any]]:
    """`readMemoriesForSurfacing`: each file's head (line and byte caps), with a note when cut."""
    out: list[dict[str, Any]] = []
    for memory in selected:
        try:
            with open(memory.path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        lines = text.split("\n")
        head = "\n".join(lines[:MAX_MEMORY_LINES])
        by_bytes = len(head.encode("utf-8")) > MAX_MEMORY_BYTES
        if by_bytes:
            head = head.encode("utf-8")[:MAX_MEMORY_BYTES].decode("utf-8", errors="ignore")
            head = head[:head.rfind("\n")] if "\n" in head else head
        truncated = len(lines) > MAX_MEMORY_LINES or by_bytes
        content = head
        if truncated:
            why = f"{MAX_MEMORY_BYTES} byte limit" if by_bytes else f"first {MAX_MEMORY_LINES} lines"
            content = (f"{head}\n\n> This memory file was truncated ({why}). Use the {FILE_READ_TOOL_NAME} tool to "
                       f"view the complete file at: {memory.path}")
        out.append({"path": memory.path, "content": content, "mtimeMs": memory.mtime_ms,
                    "header": memory_header(memory.path, memory.mtime_ms),
                    "limit": head.count("\n") + 1 if truncated else None})
    return out


def collect_recent_successful_tools(messages: Sequence[Any], last_user_index: int) -> list[str]:
    """`collectRecentSuccessfulTools`: tools of this turn that succeeded and never failed."""
    names: dict[str, str] = {}
    errored: dict[str, bool] = {}
    for message in messages[last_user_index:]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                names[str(call["id"])] = call["name"]
        elif isinstance(message, ToolMessage):
            errored[message.tool_call_id] = message.status == "error"
    failed = {names[i] for i, bad in errored.items() if bad and i in names}
    succeeded = [name for i, name in names.items() if errored.get(i) is False]
    return list(dict.fromkeys(name for name in succeeded if name not in failed))


@dataclass
class MemoryPrefetch:
    """`MemoryPrefetch`: the selection running in the background, consumed at most once."""

    task: asyncio.Task[list[Attachment]]
    consumed: bool = field(default=False)


def _last_user_prompt(messages: Sequence[Any]) -> tuple[int, str] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get("is_meta"):
            content = message.content
            text = content if isinstance(content, str) else " ".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict))
            return index, text
    return None


async def get_relevant_memory_attachments(
    query: str, memory_dir: str, select: Select, recent_tools: Sequence[str], already_surfaced: frozenset[str],
) -> list[Attachment]:
    """`getRelevantMemoryAttachments`: the selection, read, as one `relevant_memories` attachment."""
    selected = await find_relevant_memories(query, memory_dir, select, recent_tools, already_surfaced)
    memories = await asyncio.to_thread(read_memories_for_surfacing,
                                       [m for m in selected if m.path not in already_surfaced][:MAX_SELECTED])
    return [Attachment("relevant_memories", {"memories": memories})] if memories else []


def start_relevant_memory_prefetch(messages: Sequence[Any], context: Any,
                                   select: Select | None = None) -> MemoryPrefetch | None:
    """
    `startRelevantMemoryPrefetch`: once per user turn, when auto memory and the relevance opt-in
    are on, the last real prompt has more than one word, and the session budget is not spent.
    """
    from app.utils.memory_file_detection import auto_memory_dir

    if not is_relevance_search_enabled():
        return None
    memory_dir = auto_memory_dir(context)
    last = _last_user_prompt(messages)
    if memory_dir is None or last is None or not any(c.isspace() for c in last[1].strip()):
        return None
    surfaced, total = collect_surfaced_memories(messages)
    if total >= MAX_SESSION_BYTES:
        return None
    selector = select or default_select(context)
    recent = collect_recent_successful_tools(messages, last[0])
    task = asyncio.ensure_future(get_relevant_memory_attachments(last[1], memory_dir, selector, recent, surfaced))
    return MemoryPrefetch(task)


def filter_duplicate_memory_attachments(attachments: Sequence[Attachment], context: Any) -> list[Attachment]:
    """
    `filterDuplicateMemoryAttachments`: drop memories the model already has (read, written, or
    surfaced before), then mark the survivors as read so they are not surfaced again.
    """
    from app.tools.utils import read_state_key

    out: list[Attachment] = []
    for attachment in attachments:
        if attachment.type != "relevant_memories":
            out.append(attachment)
            continue
        fresh = [m for m in attachment.data["memories"]
                 if read_state_key(context, m["path"]) not in context.read_file_state]
        for memory in fresh:
            context.read_file_state[read_state_key(context, memory["path"])] = FileState(
                content=memory["content"], limit=memory["limit"])
        if fresh:
            out.append(Attachment("relevant_memories", {"memories": fresh}))
    return out


def consume_memory_prefetch(prefetch: MemoryPrefetch | None, context: Any) -> list[Attachment]:
    """The query loop's collect point: the attachments once the prefetch settled, never waiting."""
    if prefetch is None or prefetch.consumed or not prefetch.task.done():
        return []
    prefetch.consumed = True
    if prefetch.task.cancelled() or prefetch.task.exception() is not None:
        return []
    return filter_duplicate_memory_attachments(prefetch.task.result(), context)


def default_select(context: Any) -> Select:
    """
    The small fast model on this repo's model seam, as WebFetch's secondary model.
    **Exempt like the seam it wraps.**
    """
    from app.config.config import Config
    from app.utils.hooks.exec_prompt_hook import (
        build_prompt_model_caller,
        get_small_fast_model,
    )
    from app.utils.model.model import provider_for_model

    model = get_small_fast_model(str(getattr(context, "main_loop_model", None) or Config.MODEL))
    caller = build_prompt_model_caller(provider_for_model(model, default=Config.CHAT_MODEL))

    async def select(system: str, user: str) -> str:  # pragma: no cover - live provider
        return await caller([HumanMessage(content=user)], system, model, {})

    return select


__all__ = [
    "MAX_SESSION_BYTES",
    "MemoryPrefetch",
    "collect_surfaced_memories",
    "consume_memory_prefetch",
    "memory_header",
    "read_memories_for_surfacing",
    "start_relevant_memory_prefetch",
]
