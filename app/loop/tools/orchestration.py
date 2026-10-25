"""
Tool call partitioning, concurrency, and context modifier replay (Parts B5 and B6).

Ported from:
- `reference/claude-code/src/services/tools/toolOrchestration.ts` -> `partitionToolCalls`,
  `runTools`, `getMaxToolUseConcurrency`

Concurrent results are emitted **in completion order**, which is upstream's behaviour here:
`runTools` yields through `all()` as each generator finishes and `query.ts` appends them in
arrival order with no sort. (The streaming executor is the path that preserves the model's
call order — see `streaming/executor.py::get_completed_results`, which stops scanning at the
first still-running non-concurrency-safe tool.) Context modifiers are the exception and are
replayed in call order, because a modifier out of order changes what the next tool sees.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage

from app.loop.messages import Message, ToolCall
from app.loop.tools.base import Tool, find_tool_by_name
from app.loop.tools.execution import (
    ToolExecutionOutcome,
    run_tool_use,
    yield_missing_tool_results,
)
from app.loop.tools.validation import validate_against_schema

#: `toolOrchestration.ts` -> `getMaxToolUseConcurrency`'s fallback.
DEFAULT_MAX_TOOL_USE_CONCURRENCY = 10

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


def get_max_tool_use_concurrency() -> int:
    """
    `toolOrchestration.ts` -> `getMaxToolUseConcurrency`.

    Upstream writes `parseInt(env) || 10`, so a 0 or an unparseable value both fall back to
    10 rather than disabling concurrency — the `||` is not a typo for `??`.
    """
    raw = os.environ.get("TDDAGENTS_MAX_TOOL_USE_CONCURRENCY", "")
    match = re.match(r"\s*([+-]?[0-9]+)", raw)
    parsed = int(match.group(1)) if match else 0
    return parsed or DEFAULT_MAX_TOOL_USE_CONCURRENCY


def partition_tool_calls(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[Tool],
) -> list[ToolBatch]:
    """
    Partition tool calls into batches (Part B5).

    Each batch is either:
    1. A single non-concurrency-safe tool call, or
    2. Multiple consecutive concurrency-safe tool calls.

    **Schema validation runs before `is_concurrency_safe`**, as upstream: the predicate is
    only ever asked about input that parsed, so a tool cannot be handed a shape its own
    classifier was not written for. The parse result is then discarded — `run_tool_use`
    validates again for real, and upstream parses twice here for exactly this reason.

    Three paths fail closed to "not safe", so an unclassifiable call runs alone: the tool is
    unknown, its input does not match the schema, or the predicate raises.
    """
    batches: list[ToolBatch] = []

    for call in tool_calls:
        tool_name = str(call.get("name") or "")
        tool = find_tool_by_name(tools, tool_name)
        args = call.get("args") or {}
        if tool is None or validate_against_schema(tool_name, getattr(tool, "input_schema", None), args) is not None:
            is_safe = False
        else:
            try:
                is_safe = bool(tool.is_concurrency_safe(args))
            except Exception:  # noqa: BLE001 - deliberate catch-all (fail-closed / upstream catch)
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
        # Upstream `options.mainLoopModel`: a skill's `model:` override (Phase 8).
        if hasattr(new_ctx, "main_loop_model") and hasattr(context, "main_loop_model"):
            context.main_loop_model = new_ctx.main_loop_model
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
                # Concurrent batch. Results are yielded **as they complete**, which is what
                # upstream's `all()` does and what nothing downstream re-sorts. Only the
                # context modifiers are put back into the model's call order, below — that
                # replay is the one place ordering is restored by hand, because a modifier
                # applied out of order would change what the *next* tool sees.
                for call in batch.calls:
                    cid = str(call.get("id") or "")
                    context.in_flight_tool_ids.add(cid)

                queued_modifiers: dict[str, list[Callable[[ToolContext], ToolContext]]] = {}
                semaphore = asyncio.Semaphore(get_max_tool_use_concurrency())

                async def execute_concurrent(c: ToolCall) -> tuple[str, ToolExecutionOutcome]:
                    cid_inner = str(c.get("id") or "")
                    try:
                        async with semaphore:  # noqa: B023 - closure runs within the same iteration
                            outcome = await run_tool_use(c, assistant_message, context, available_tools)
                        return cid_inner, outcome
                    finally:
                        context.in_flight_tool_ids.discard(cid_inner)

                pending = [asyncio.ensure_future(execute_concurrent(c)) for c in batch.calls]
                try:
                    for finished in asyncio.as_completed(pending):
                        cid, outcome = await finished
                        completed_tool_ids.add(cid)
                        yield outcome.message
                        for extra in outcome.extra_messages:
                            yield extra
                        if outcome.context_modifier is not None:
                            queued_modifiers.setdefault(cid, []).append(
                                outcome.context_modifier.modify_context
                            )
                finally:
                    for task in pending:
                        if not task.done():
                            task.cancel()

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
                    for extra in outcome.extra_messages:
                        yield extra

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
