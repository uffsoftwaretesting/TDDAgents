"""
Tool call partitioning, concurrency, and context modifier replay (Parts B5 and B6).

Ported from:
- `reference/claude-code/src/services/tools/toolOrchestration.ts` -> `partitionToolCalls`, `runTools`
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Callable, Sequence

from langchain_core.messages import AIMessage

from app.loop.messages import Message, ToolCall
from app.loop.tools.base import Tool, find_tool_by_name
from app.loop.tools.execution import (
    ToolExecutionOutcome,
    run_tool_use,
    yield_missing_tool_results,
)

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.context import ToolContext
    from app.loop.state import LoopState


@dataclass(frozen=True, slots=True)
class ToolBatch:
    """
    A batch of tool calls to run either concurrently or serially.
    """

    is_concurrency_safe: bool
    calls: tuple[ToolCall, ...]


def partition_tool_calls(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[Tool],
) -> list[ToolBatch]:
    """
    Partition tool calls into batches (Part B5).

    Each batch is either:
    1. A single non-concurrency-safe tool call, or
    2. Multiple consecutive concurrency-safe tool calls.

    Fail-closed: if tool is not found, or is_concurrency_safe raises, it is treated as False.
    """
    batches: list[ToolBatch] = []

    for call in tool_calls:
        tool = find_tool_by_name(tools, str(call.get("name") or ""))
        if tool is None:
            is_safe = False
        else:
            try:
                is_safe = bool(tool.is_concurrency_safe(call.get("args") or {}))
            except Exception:
                is_safe = False

        if is_safe and batches and batches[-1].is_concurrency_safe:
            prev = batches[-1]
            batches[-1] = ToolBatch(is_concurrency_safe=True, calls=(*prev.calls, call))
        else:
            batches.append(ToolBatch(is_concurrency_safe=is_safe, calls=(call,)))

    return batches


def apply_context_modifier(
    context: ToolContext,
    modifier: Callable[[ToolContext], ToolContext],
) -> ToolContext:
    """
    Apply a context modifier function to a ToolContext.

    If the modifier returns a new ToolContext object, update the fields of context in place.
    """
    new_ctx = modifier(context)
    if new_ctx is not context:
        context.cancel = new_ctx.cancel
        context.get_app_state = new_ctx.get_app_state
        context.set_app_state = new_ctx.set_app_state
        context.messages = new_ctx.messages
        context.in_flight_tool_ids = new_ctx.in_flight_tool_ids
        if hasattr(new_ctx, "tools") and hasattr(context, "tools"):
            context.tools = new_ctx.tools
        if hasattr(new_ctx, "permission_context") and hasattr(context, "permission_context"):
            context.permission_context = new_ctx.permission_context
    return context


async def run_tools(
    tool_calls: tuple[ToolCall, ...],
    assistant_messages: tuple[Message, ...],
    state: LoopState,
    config: RunConfig,
    *,
    tools: Sequence[Tool] | None = None,
) -> AsyncIterator[Message]:
    """
    Execute tool calls, coordinating serial/concurrent batches and context replay (Parts B5, B6, B7).
    """
    context = state.tool_context
    available_tools: Sequence[Tool] = (
        tools if tools is not None else getattr(context, "tools", ())
    )
    assistant_message = assistant_messages[-1] if assistant_messages else AIMessage(content="")

    completed_tool_ids: set[str] = set()
    batches = partition_tool_calls(tool_calls, available_tools)
    is_generator_exit = False

    try:
        for batch in batches:
            if context.cancel.cancelled:
                break

            if batch.is_concurrency_safe:
                # Concurrent batch execution
                for call in batch.calls:
                    cid = str(call.get("id") or "")
                    context.in_flight_tool_ids.add(cid)

                queued_modifiers: dict[str, list[Callable[[ToolContext], ToolContext]]] = {}

                async def execute_concurrent(c: ToolCall) -> ToolExecutionOutcome:
                    cid_inner = str(c.get("id") or "")
                    try:
                        return await run_tool_use(c, assistant_message, context, available_tools)
                    finally:
                        context.in_flight_tool_ids.discard(cid_inner)

                outcomes = await asyncio.gather(*(execute_concurrent(c) for c in batch.calls))

                for call, outcome in zip(batch.calls, outcomes):
                    cid = str(call.get("id") or "")
                    completed_tool_ids.add(cid)
                    yield outcome.message
                    if outcome.context_modifier is not None:
                        queued_modifiers.setdefault(cid, []).append(outcome.context_modifier.modify_context)

                # Part B6: Replay context modifiers strictly in the model's call order
                for call in batch.calls:
                    cid = str(call.get("id") or "")
                    for mod in queued_modifiers.get(cid, []):
                        apply_context_modifier(context, mod)

            else:
                # Serial batch execution
                for call in batch.calls:
                    if context.cancel.cancelled:
                        break

                    cid = str(call.get("id") or "")
                    context.in_flight_tool_ids.add(cid)
                    try:
                        outcome = await run_tool_use(call, assistant_message, context, available_tools)
                    finally:
                        context.in_flight_tool_ids.discard(cid)

                    completed_tool_ids.add(cid)
                    yield outcome.message

                    if outcome.context_modifier is not None:
                        apply_context_modifier(context, outcome.context_modifier.modify_context)

    except GeneratorExit:
        is_generator_exit = True
        raise
    finally:
        if not is_generator_exit:
            # Part B7: History-repair invariant
            is_cancelled = context.cancel.cancelled
            for missing in yield_missing_tool_results(tool_calls, completed_tool_ids, cancelled=is_cancelled):
                completed_tool_ids.add(str(missing.tool_call_id))
                yield missing
