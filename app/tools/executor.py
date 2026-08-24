"""
Batch execution of the tool calls in one model turn.

Ported from claude-code's `toolOrchestration.ts`. The partitioning rule is small enough to
state completely: consecutive concurrency-safe calls form one batch that runs in parallel,
and anything else becomes a batch of one that runs alone. That single rule is what keeps a
read fan-out fast while guaranteeing a write is never racing anything — including another
write, and including a read that would otherwise observe the workspace mid-change.

Two deliberate divergences from the original, both settled with the user:

* **No sibling abort.** A failing tool yields `ToolResult(is_error=True)` and its siblings
  run to completion, exactly as upstream does. Cancelling them would discard sandbox round
  trips already paid for, and would make which results exist depend on race timing.
* **Deterministic reassembly.** Results are re-sorted into the model's original call order
  before they are returned. Wall-clock interleaving still varies run to run, but the
  message list and the event log do not — which is what the three-runs-per-task research
  design needs in order to compare like with like.

There is no streaming here. `run_agent` (Phase 2) calls `llm.invoke()` and hands this
module a complete list of calls.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.config.config import Config
from app.tools.base import AnyTool, ToolContext, ToolResult, execute_tool
from app.tools.registry import ToolRegistry

logger = logging.getLogger("TDDOrchestrator.Tools")


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the model emitted it."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    index: int = 0


@dataclass(frozen=True)
class Batch:
    """A run of calls that may execute together, or a single call that may not."""

    is_concurrency_safe: bool
    calls: tuple[ToolCall, ...]


def _is_concurrency_safe(call: ToolCall, tool: AnyTool | None) -> bool:
    """
    Whether this specific call may run alongside its neighbours.

    Fails closed at every step: an unknown tool, arguments that do not parse, or a
    predicate that raises all mean "not safe". The original catches a throwing
    `isConcurrencySafe` for the same reason — a shell-quote parse failure inside Bash's
    read-only check must not be read as permission to parallelize.
    """
    if tool is None:
        return False
    try:
        args = tool.args_schema.model_validate(call.args)
    except Exception:
        return False
    try:
        return bool(tool.is_concurrency_safe(args))
    except Exception:
        logger.debug("is_concurrency_safe raised for %s; treating the call as unsafe.", call.name)
        return False


def partition_tool_calls(calls: list[ToolCall], registry: ToolRegistry) -> list[Batch]:
    """
    Groups calls into batches: consecutive concurrency-safe ones together, everything else
    alone.

    Order is preserved. A write in the middle of five reads splits them into
    `[reads] [write] [reads]` rather than reordering to batch the reads together — the
    model asked for that sequence, and the second group may well depend on the write.
    """
    batches: list[Batch] = []
    for call in calls:
        safe = _is_concurrency_safe(call, registry.get(call.name))
        if safe and batches and batches[-1].is_concurrency_safe:
            batches[-1] = Batch(True, batches[-1].calls + (call,))
        else:
            batches.append(Batch(safe, (call,)))
    return batches


def _unknown_tool(call: ToolCall, registry: ToolRegistry) -> ToolResult:
    return ToolResult(
        tool_name=call.name,
        content=(
            f"Unknown tool '{call.name}'. Available tools: {', '.join(registry.names())}."
        ),
        is_error=True,
        call_index=call.index,
        call_id=call.id,
    )


def _cancelled(call: ToolCall) -> ToolResult:
    """
    A placeholder for a call that never ran.

    Every tool call needs a matching result: a message list with a dangling tool call is
    rejected outright by the provider, so a cancelled batch still has to account for
    itself.
    """
    return ToolResult(
        tool_name=call.name,
        content="Cancelled before execution.",
        is_error=True,
        call_index=call.index,
        call_id=call.id,
    )


def _run_one(call: ToolCall, registry: ToolRegistry, ctx: ToolContext) -> ToolResult:
    tool = registry.get(call.name)
    if tool is None:
        return _unknown_tool(call, registry)
    return execute_tool(tool, call.args, ctx, call_index=call.index, call_id=call.id)


def execute_tool_calls(
    calls: list[ToolCall],
    registry: ToolRegistry,
    ctx: ToolContext,
) -> list[ToolResult]:
    """
    Runs every call in one turn and returns the results in the model's original order.

    Cancellation is cooperative and checked between batches: a batch already in flight
    finishes, and everything after it is answered with a cancellation placeholder rather
    than silently dropped.
    """
    if not calls:
        return []

    indexed = [
        call if call.index else ToolCall(call.name, call.args, call.id, position)
        for position, call in enumerate(calls)
    ]

    results: list[ToolResult] = []

    for batch in partition_tool_calls(indexed, registry):
        # The token is the only latch needed: once tripped it stays tripped, so a separate
        # local flag would just be a second copy of the same fact.
        if ctx.cancel_token.cancelled:
            results.extend(_cancelled(call) for call in batch.calls)
            continue

        if batch.is_concurrency_safe and len(batch.calls) > 1:
            workers = min(len(batch.calls), Config.MAX_TOOL_CONCURRENCY)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results.extend(pool.map(lambda call: _run_one(call, registry, ctx), batch.calls))
        else:
            for call in batch.calls:
                results.append(_run_one(call, registry, ctx))

    # The model's order, not the completion order. This is what makes a run replayable.
    results.sort(key=lambda result: result.call_index)
    return results
