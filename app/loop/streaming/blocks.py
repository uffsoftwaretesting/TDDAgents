"""
Content-block framing over a provider stream (Phase 1).

Ported from `reference/claude-code/src/services/api/claude.ts` -> `queryModel`'s
`content_block_start` / `content_block_delta` / `content_block_stop` handling, which emits
**one complete assistant message per content block** rather than one per response:

    case 'content_block_stop': {
      const m: AssistantMessage = { message: { ...partialMessage,
        content: normalizeContentFromAPI([contentBlock] ...) }, ... }
      newMessages.push(m); yield m

That framing is the whole reason `StreamingToolExecutor` can start a tool while the rest of
the answer is still arriving (Part F2): a tool call is complete and dispatchable the moment
its own block closes, and waiting for the response to end would make eager dispatch a
no-op. A turn answering with text and two tool calls produces three messages.

**Where the port has to diverge.** The Anthropic SDK marks block boundaries explicitly;
LangChain's `astream` yields `AIMessageChunk`s with no such markers. A block is therefore
inferred: a chunk's text belongs to the text block, a chunk's `tool_call_chunks` belong to
the block named by their `index`, and a block closes when a *different* block's content
arrives, or when the stream ends. That is the same sequencing the SDK's events describe,
read off the only signal the provider gives.

Chunks that carry neither text nor tool-call data — the final `finish_reason`/usage chunk —
open nothing. They are merged into whatever block is open, so metadata rides along without
producing an empty message. Token usage does not depend on this: it is collected by the
callback handler on the model call itself.
"""

from __future__ import annotations

from typing import Any, AsyncIterable, AsyncIterator, Iterator, cast

from langchain_core.messages.tool import ToolCallChunk

from langchain_core.messages import AIMessageChunk, BaseMessage, message_chunk_to_message

#: Identifies the block a piece of a chunk belongs to: the text block, or one tool call.
BlockKey = tuple[str, int]

TEXT_BLOCK: BlockKey = ("text", -1)


def _tool_call_chunks(chunk: AIMessageChunk) -> list[dict[str, Any]]:
    raw = getattr(chunk, "tool_call_chunks", None) or []
    return [c for c in raw if isinstance(c, dict)]


def _has_text(chunk: AIMessageChunk) -> bool:
    content = chunk.content
    if isinstance(content, str):
        return bool(content)
    return bool(content)


def split_chunk_into_blocks(chunk: AIMessageChunk) -> list[tuple[BlockKey, AIMessageChunk]]:
    """
    Split one streamed chunk into the content blocks it contributes to.

    A chunk usually touches exactly one block, but a provider is free to put text and the
    start of a tool call in the same frame, and the SDK's own events would separate those.
    The text part keeps the original chunk's metadata so nothing is lost when only one part
    survives; the tool parts carry just their own call.
    """
    calls = _tool_call_chunks(chunk)
    if not calls:
        return [(TEXT_BLOCK, chunk)]

    parts: list[tuple[BlockKey, AIMessageChunk]] = []
    if _has_text(chunk):
        parts.append((TEXT_BLOCK, AIMessageChunk(content=chunk.content)))

    by_index: dict[int, list[dict[str, Any]]] = {}
    for call in calls:
        index = call.get("index")
        by_index.setdefault(index if isinstance(index, int) else 0, []).append(call)

    for index, grouped in by_index.items():
        chunks = cast("list[ToolCallChunk]", grouped)
        parts.append((("tool", index), AIMessageChunk(content="", tool_call_chunks=chunks)))
    return parts


def _is_empty(chunk: AIMessageChunk) -> bool:
    return not _has_text(chunk) and not _tool_call_chunks(chunk)


class BlockAccumulator:
    """
    Frames a chunk stream into whole messages, one per content block.

    Only one block is open at a time, exactly as `content_block_start`/`_stop` guarantee
    upstream: anything belonging to a different block closes the open one first.
    """

    def __init__(self) -> None:
        self._key: BlockKey | None = None
        self._chunk: AIMessageChunk | None = None

    def add(self, chunk: AIMessageChunk) -> Iterator[BaseMessage]:
        """Feed one streamed chunk; yields every block it closed."""
        if _is_empty(chunk):
            # Metadata-only frame: it belongs to the open block, and opens none of its own.
            if self._chunk is not None:
                self._chunk = self._chunk + chunk
            return

        for key, part in split_chunk_into_blocks(chunk):
            if self._key is not None and key != self._key:
                yield from self.flush()
            if self._chunk is None:
                self._key, self._chunk = key, part
            else:
                self._chunk = self._chunk + part

    def flush(self) -> Iterator[BaseMessage]:
        """Close the open block, if any — the `content_block_stop` of the stream's end."""
        if self._chunk is not None:
            yield message_chunk_to_message(self._chunk)
        self._key, self._chunk = None, None


async def frame_content_blocks(stream: AsyncIterable[Any]) -> AsyncIterator[BaseMessage]:
    """
    Turn a provider stream into complete assistant messages, one per content block.

    A non-chunk `BaseMessage` arriving mid-stream closes the open block before it is passed
    through, so ordering is never rearranged. Anything that is neither is ignored, as
    upstream ignores the raw `stream_event` passthroughs it re-yields for the UI.
    """
    accumulator = BlockAccumulator()
    async for item in stream:
        if isinstance(item, AIMessageChunk):
            for message in accumulator.add(item):
                yield message
            continue
        if isinstance(item, BaseMessage):
            for message in accumulator.flush():
                yield message
            yield item
    for message in accumulator.flush():
        yield message


__all__ = [
    "BlockAccumulator",
    "BlockKey",
    "TEXT_BLOCK",
    "frame_content_blocks",
    "split_chunk_into_blocks",
]
