"""
The streamed model call the loop consumes (Part F1): `deps.call_model(state, config)`.

Request shape follows claude-code's `queryModel` path: the system prompt is
`getSystemPrompt` sections with `appendSystemContext`; the history is preceded by
`prependUserContext` (memory files and date). Attachments (the TDD state among them) are
persisted into the stored history by the engine's attachment pipeline, not added here.

The provider and model id are resolved from `Config.CHAT_MODEL` / `Config.MODEL`, the one
place the rest of the pipeline resolves them; this module never names a model itself.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from langchain_core.messages import SystemMessage

from app.config.config import Config
from app.loop.compact.compact import is_compact_boundary_message
from app.loop.config import RunConfig
from app.loop.context.assembly import (
    append_system_context,
    get_session_context,
    get_system_prompt,
    prepend_user_context,
)
from app.loop.context.cache import prepare_cached_request
from app.loop.hooks.messages import reorder_attachments_for_api
from app.loop.messages import Message
from app.loop.model_aliases import provider_for_model
from app.loop.prompts.sections import resolve_system_prompt_sections
from app.loop.retry import RetryContext, RetryOptions, with_retry
from app.loop.state import LoopState
from app.loop.streaming.blocks import frame_content_blocks
from app.loop.tools.base import Tool
from app.utils.chat_model_factory import get_chat_model
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
    history = prepend_user_context(reorder_attachments_for_api(sendable), request_user_context(state, config))
    return [system, *history]


_global_token_tracker = GlobalTokenTracker()


def get_global_token_tracker() -> GlobalTokenTracker:
    """Return the global token tracker instance (Plan D1)."""
    return _global_token_tracker


def get_token_usage_summary() -> dict[str, Any]:
    """Return structured token usage metrics summary (Plan D1)."""
    return _global_token_tracker.summary()


async def accumulate_stream(stream: AsyncIterable[Any]) -> AsyncIterator[Message]:
    """
    Turn a provider stream into complete assistant messages, one per content block.

    Thin alias for `frame_content_blocks`, which ports upstream's `content_block_stop`
    emission. The engine dispatches tools from every message it receives (Part F2), so it
    must never see a partial chunk — an `AIMessageChunk` mid-stream carries tool calls with
    an empty name or truncated arguments — and it must see a finished tool call as soon as
    that call's own block closes, not when the whole answer ends.
    """
    async for message in frame_content_blocks(stream):
        yield message


def open_model_stream(state: LoopState, config: RunConfig, context: RetryContext) -> AsyncIterable[Any]:
    """
    Build the request and open the provider stream. One attempt, no iteration.

    This is `with_retry`'s `operation`: everything that can fail before a single token has
    been emitted. `RetryContext` carries what the previous failure asked to change — today
    only the model, which the 529 fallback swaps.
    """
    # The provider follows the model: a worker resolved to a Claude alias reaches Anthropic
    # even when the run's configured provider is OpenAI (`model_aliases.py`).
    provider = provider_for_model(context.model, default=Config.CHAT_MODEL)
    messages_to_send, cache_kwargs = prepare_cached_request(
        build_request_messages(state, config),
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
