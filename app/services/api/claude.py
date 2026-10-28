"""
The streamed model call the loop consumes (Part F1): `deps.call_model(state, config)`.

Request shape follows claude-code's `queryModel` path: the system prompt is
`getSystemPrompt` sections with `appendSystemContext`; the history is preceded by
`prependUserContext` (memory files and date). Attachments (the TDD state among them) are
persisted into the stored history by the engine's attachment pipeline, not added here.

The provider and model id are resolved from `Config.CHAT_MODEL` / `Config.MODEL`, the one
place the rest of the pipeline resolves them; this module never names a model itself.

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

Prompt-cache breakpoints (`claude.ts` -> `buildSystemPromptBlocks`, `getCacheControl`,
`addCacheBreakpoints`): upstream's two markers, the system block and the last message, for
Anthropic; nothing for OpenAI, which caches prefixes unaided.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterator, Sequence
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    SystemMessage,
    message_chunk_to_message,
)
from langchain_core.messages.tool import ToolCallChunk

from app.config.config import Config
from app.constants.system_prompt_sections import (
    resolve_system_prompt_sections,
)
from app.context import (
    append_system_context,
    get_session_context,
    get_system_prompt,
    prepend_user_context,
)
from app.query.config import RunConfig
from app.query.state import LoopState
from app.services.api.with_retry import RetryContext, RetryOptions, with_retry
from app.services.compact.compact import is_compact_boundary_message
from app.tool import Tool
from app.utils.chat_model_factory import get_chat_model
from app.utils.messages import (
    Message,
    merge_split_assistant_messages,
    move_tool_result_media_to_user,
    reorder_attachments_for_api,
)
from app.utils.model.model import provider_for_model
from app.utils.token_metrics import GlobalTokenTracker

logger = logging.getLogger(__name__)


def convert_tool_to_langchain(tool: Tool) -> dict[str, Any]:
    """Convert a loop tool into an OpenAI-style function tool dictionary."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.prompt,
            "parameters": tool.input_schema,
        },
    }


def request_system_context(state: LoopState, config: RunConfig) -> dict[str, str]:
    """
    The run's memoized system context, as this context sends it: without `gitStatus` for an
    agent that omits it (`runAgent.ts` -> `systemContextNoGit`). Filtered per request, so a
    worker sharing the run's session never changes what the main thread sends.
    """
    ctx = state.tool_context
    context = dict(get_session_context(config.run_id, ctx.workspace).system_context)
    if ctx.omit_git_status:
        context.pop("gitStatus", None)
    return context


def request_user_context(state: LoopState, config: RunConfig) -> dict[str, str]:
    """
    The run's memoized user context, as this context sends it: without `claudeMd` when the
    agent omits it (`shouldOmitClaudeMd`), then any extra keys (`getCoordinatorUserContext`).
    """
    ctx = state.tool_context
    context = dict(get_session_context(config.run_id, ctx.workspace).user_context)
    if ctx.omit_claude_md:
        context.pop("claudeMd", None)
    context.update(ctx.extra_user_context or {})
    return context


def request_system_texts(state: LoopState, config: RunConfig) -> list[str]:
    """
    The system prompt sections with the system context appended (`appendSystemContext`).

    A context with its own `agent_system_prompt` sends that instead of the default
    sections, as `runAgent` hands `query()` the agent's prompt. The default sections stay
    memoized in the run's section cache, which an override never touches.
    """
    ctx = state.tool_context
    if ctx.agent_system_prompt is not None:
        sections = list(ctx.agent_system_prompt)
    else:
        session = get_session_context(config.run_id, ctx.workspace)
        sections = resolve_system_prompt_sections(
            get_system_prompt(ctx.workspace, Config.MODEL, ctx.mcp_clients), session.section_cache)
    return append_system_context(sections, request_system_context(state, config))


def build_request_messages(state: LoopState, config: RunConfig) -> list[Message]:
    """
    System prompt + system context, then the user context and the stored history.

    Everything that reshapes history — the tool-result budget, microcompact, autocompact,
    attachments — acts on the *stored* messages in the engine, as upstream's
    `messagesForQuery` pipeline does, so the request adds nothing per call and its prefix
    stays byte-stable between turns.
    """
    system = SystemMessage(content="\n\n".join(request_system_texts(state, config)))
    # The compact boundary is a bookkeeping record (upstream's `system` message), never input.
    sendable = [m for m in state.messages if not is_compact_boundary_message(m)]
    history = prepend_user_context(
        reorder_attachments_for_api(merge_split_assistant_messages(sendable)), request_user_context(state, config))
    return [system, *history]


_global_token_tracker = GlobalTokenTracker()


def get_global_token_tracker() -> GlobalTokenTracker:
    """Return the global token tracker instance (Plan D1)."""
    return _global_token_tracker


def open_model_stream(state: LoopState, config: RunConfig, context: RetryContext) -> AsyncIterable[Any]:
    """
    Build the request and open the provider stream. One attempt, no iteration.

    This is `with_retry`'s `operation`: everything that can fail before a single token has
    been emitted. `RetryContext` carries what the previous failure asked to change — today
    only the model, which the 529 fallback swaps.
    """
    # The provider follows the model: a worker resolved to a Claude alias reaches Anthropic
    # even when the run's configured provider is OpenAI (`utils/model/model.py`).
    provider = provider_for_model(context.model, default=Config.CHAT_MODEL)
    request = build_request_messages(state, config)
    if provider != "anthropic":
        request = move_tool_result_media_to_user(request)
    messages_to_send, cache_kwargs = prepare_cached_request(
        request,
        provider=provider,
        system_texts=request_system_texts(state, config),
    )
    lc_tools = [convert_tool_to_langchain(t) for t in state.tool_context.tools]

    model = get_chat_model(provider, model=context.model, temperature=Config.TEMPERATURE)
    if lc_tools:
        model = model.bind_tools(lc_tools)

    callbacks: list[Any] = [_global_token_tracker]
    cost_tracker = state.tool_context.cost_tracker
    if cost_tracker is not None:
        callbacks.append(cost_tracker)
    try:
        stream: AsyncIterable[Any] = model.astream(
            messages_to_send, config={"callbacks": callbacks}, **cache_kwargs
        )
    except TypeError:
        stream = model.astream(messages_to_send, **cache_kwargs)
    return stream


async def stream_call_model(state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
    """
    Real `call_model`: assemble the request, bind the context's tools, stream the reply.

    **Retry covers the opening of the stream only.** Upstream wraps `withRetry` around
    request creation and lets the stream body fail into a separate non-streaming fallback
    (`claude.ts`: the retry generator settles at response-headers-received, the
    `for await` over the stream sits outside it). LangChain fuses request and stream into
    one generator, so the achievable equivalent is to retry while nothing has been emitted
    and to let a mid-stream failure raise: once a block has reached the loop, a retry would
    duplicate content or orphan a tool call that was already dispatched.
    """
    async def operation(attempt: int, context: RetryContext) -> AsyncIterable[Any]:
        stream = open_model_stream(state, config, context)
        # Pull the first item inside the retried operation, so a provider that fails at
        # connect or on the first frame is still "before any output" and may be retried.
        iterator = stream.__aiter__()
        try:
            first = await iterator.__anext__()
        except StopAsyncIteration:
            return _empty_stream()
        return _restream(first, iterator)

    opened = await with_retry(
        operation,
        RetryOptions(
            # Upstream `options.mainLoopModel`: a skill's override wins over the run's model.
            model=state.tool_context.main_loop_model or config.model,
            max_retries=config.max_retries,
            fallback_model=config.fallback_model,
            on_notice=lambda notice: logger.warning(
                "Model call attempt %d/%d failed: %s",
                notice.attempt, notice.max_retries + 1, notice.error,
            ),
            is_cancelled=lambda: state.tool_context.cancel.cancelled,
        ),
    )

    async for message in frame_content_blocks(opened):
        yield message


async def _empty_stream() -> AsyncIterator[Any]:
    """A provider that closed without emitting anything. Yields nothing."""
    return
    yield  # pragma: no cover - unreachable, makes this an async generator


async def _restream(first: Any, rest: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Put back the item the retry gate consumed, then hand over the rest of the stream."""
    yield first
    async for item in rest:
        yield item


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
    # Upstream's `message.id`: every block of this response carries it, so the request builder
    # can merge them back into one assistant message (`merge_split_assistant_messages`).
    response_id = f"msg_{uuid.uuid4().hex}"

    def stamped(message: BaseMessage) -> BaseMessage:
        return message.model_copy(update={"id": response_id}) if isinstance(message, AIMessage) else message

    async for item in stream:
        if isinstance(item, AIMessageChunk):
            for message in accumulator.add(item):
                yield stamped(message)
            continue
        if isinstance(item, BaseMessage):
            for message in accumulator.flush():
                yield stamped(message)
            yield item
    for message in accumulator.flush():
        yield stamped(message)


# ── Phase 5: what is sent to a provider that supports cache breakpoints ────────

#: `getCacheControl()` without the 1h TTL (subscriber-only) or the `global` scope.
EPHEMERAL_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}


def get_prompt_caching_enabled() -> bool:
    """`getPromptCachingEnabled`: on unless `TDDAGENTS_DISABLE_PROMPT_CACHING` (upstream's
    `DISABLE_PROMPT_CACHING`). The per-model Haiku/Sonnet/Opus switches name Anthropic
    default models this build does not have."""
    value = os.environ.get("TDDAGENTS_DISABLE_PROMPT_CACHING")
    return not (value is not None and value.strip().lower() in ("1", "true", "yes", "on"))


def system_prompt_blocks_for_api(system_texts: Sequence[str], *, enable_caching: bool) -> list[dict[str, Any]]:
    """
    `buildSystemPromptBlocks` over `splitSysPromptPrefix`'s path without the global cache
    scope: every non-empty section joined into one `org`-scoped block. The global scope is a
    first-party beta (`shouldUseGlobalCacheScope` + its beta header) this transport does not
    send, so this build takes upstream's path for providers without it.
    """
    joined = "\n\n".join(text for text in system_texts if text)
    if not joined:
        return []
    block: dict[str, Any] = {"type": "text", "text": joined}
    if enable_caching:
        block["cache_control"] = dict(EPHEMERAL_CACHE_CONTROL)
    return [block]


def prepare_cached_request(
    messages: Sequence[Any], *, provider: str, system_texts: Sequence[str]
) -> tuple[list[Any], dict[str, Any]]:
    """
    The request with upstream's two breakpoints, for Anthropic: the system prompt as cached
    blocks (`buildSystemPromptBlocks`) and a `cache_control` call argument, which
    langchain-anthropic applies to the last eligible block of the last message
    (`addCacheBreakpoints`' marker). Any other provider, or caching disabled, gets the
    request unchanged — OpenAI, for one, caches prompt prefixes without markers.
    """
    if provider != "anthropic" or not get_prompt_caching_enabled():
        return list(messages), {}
    from langchain_core.messages import SystemMessage

    blocks = system_prompt_blocks_for_api(system_texts, enable_caching=True)
    content: list[str | dict[Any, Any]] = list(blocks)
    prepared = [SystemMessage(content=content), *messages[1:]] if blocks else list(messages)
    return prepared, {"cache_control": dict(EPHEMERAL_CACHE_CONTROL)}


__all__ = [
    "TEXT_BLOCK",
    "BlockAccumulator",
    "BlockKey",
    "frame_content_blocks",
    "split_chunk_into_blocks",
]
