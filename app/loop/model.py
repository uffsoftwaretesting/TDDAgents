"""
The streamed model call the loop consumes (Part F1): `deps.call_model(state, config)`.

Request shape follows claude-code's `queryModel` path: the system prompt is
`getSystemPrompt` sections with `appendSystemContext`; the history is preceded by
`prependUserContext` (memory files and date) and followed by any attachment this turn
produces — here the TDD state delta (`compute_tdd_state_attachment`).

The provider and model id are resolved from `Config.CHAT_MODEL` / `Config.MODEL`, the one
place the rest of the pipeline resolves them; this module never names a model itself.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterable, AsyncIterator

from langchain_core.messages import SystemMessage

from app.config.config import Config
from app.loop.config import RunConfig
from app.loop.context.assembly import (
    append_system_context,
    get_session_context,
    get_system_prompt,
    prepend_user_context,
)
from app.loop.context.attachments import compute_tdd_state_attachment
from app.loop.hooks.messages import reorder_attachments_for_api
from app.loop.messages import Message
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


def read_todo(context: Any) -> str | None:
    """
    The session's todo list, rendered for the attachment.

    It comes from `TodoWrite`'s store rather than a `TODO.md` in the workspace: a file there
    is indistinguishable from one the project itself owns, and the agent could edit its own
    checklist as ordinary content.
    """
    from app.loop.tools.todo import read_todos

    try:
        todos = read_todos(context)
    except Exception:
        return None
    if not todos:
        return None
    return "\n".join(f"- [{item.status}] {item.content}" for item in todos)


def build_request_messages(state: LoopState, config: RunConfig) -> list[Message]:
    """System prompt + system context, user context, history, then this turn's attachment."""
    from app.loop.context.compact import apply_tool_result_budget, microcompact_tool_results

    ws = state.tool_context.workspace
    session = get_session_context(config.run_id, ws)
    sections = resolve_system_prompt_sections(get_system_prompt(ws, Config.MODEL), session.section_cache)
    system = SystemMessage(content="\n\n".join(append_system_context(sections, session.system_context)))
    compacted = microcompact_tool_results(state.messages, keep_recent_turns=2)
    budgeted = apply_tool_result_budget(compacted)
    history = prepend_user_context(reorder_attachments_for_api(budgeted), session.user_context)
    attachment = compute_tdd_state_attachment(
        state.messages, state.phase_ledger, read_todo(state.tool_context)
    )
    return [system, *history, *([attachment] if attachment is not None else [])]


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
    messages_to_send = build_request_messages(state, config)
    lc_tools = [convert_tool_to_langchain(t) for t in state.tool_context.tools]

    model = get_chat_model(Config.CHAT_MODEL, model=context.model, temperature=Config.TEMPERATURE)
    if lc_tools:
        model = model.bind_tools(lc_tools)

    try:
        stream: AsyncIterable[Any] = model.astream(
            messages_to_send, config={"callbacks": [_global_token_tracker]}
        )
    except TypeError:
        stream = model.astream(messages_to_send)
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
            model=config.model,
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
