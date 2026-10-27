"""
The loop's I/O seam, so it can be driven by fakes.

Ported from `reference/claude-code/src/query/deps.ts` -> `QueryDeps` / `productionDeps`.
Explicit constructor injection rather than a container or a patching library, and the
in-source note explains why the record starts small: its scope is *"intentionally narrow
(4 deps) to prove the pattern"*, with followups adding `runTools`, `handleStopHooks`,
`logEvent` and the rest. The same applies here in reverse order — this part needs the model
call and the tool runner, and Part A4 widens the record to the full seam (compaction, uuid,
now, stop hooks, the event sink) without changing `run_loop`'s signature.

There is no `production_deps()` yet, and that absence is deliberate: the real model call is
Part F1 and the real tool runner is Part B5. A factory today could only return placeholders,
which is how a placeholder ends up in a run.

The production dependencies of the loop (`query/deps.ts` -> `productionDeps`).

Phase 5 binds the compaction slots: `microcompact` (the external build's time-based path,
off by default), `autocompact` (`autoCompactIfNeeded`), and `compact`, the reactive
compaction the engine runs after a prompt-too-long. Both compactions inject the same
post-compact attachments: the **TDD state re-attached from the ledger and the last
`RunTests` record**, never from the summary.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from app.query.config import RunConfig
from app.query.state import CompactionResult, CompactionTracking, LoopState
from app.query.stop_hooks import StopHooks, build_stop_hooks_runner
from app.services.api.claude import stream_call_model
from app.services.compact.auto_compact import AutoCompactOutcome, auto_compact_if_needed
from app.services.compact.compact import (
    build_post_compact_messages,
    compact_conversation,
)
from app.services.compact.micro_compact import microcompact_messages
from app.services.compact.summarizer import build_compact_summarizer
from app.services.tools.tool_orchestration import run_tools
from app.tdd.tdd_state_attachment import create_tdd_state_attachment_message
from app.utils.messages import Message, ToolCall

#: Call the model for one iteration and stream back what it says.
#:
#: Takes the whole `LoopState` and the run's `RunConfig` — the `(state, config)` shape the
#: upstream source anticipates when it says the split *"makes future step() extraction
#: tractable — a pure reducer can take (state, event, config)"*. Yielding rather than
#: returning is what lets Part F2 dispatch a tool the moment its content block closes,
#: while the rest of the answer is still streaming.
CallModel: TypeAlias = Callable[["LoopState", RunConfig], AsyncIterator[Message]]

#: Execute the tool calls of one iteration and stream back their results.
#:
#: Mirrors `runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)`: the
#: calls to run, the assistant messages that asked for them — a result has to be able to
#: name the message it answers — and the state and config that carry the rest.
RunTools: TypeAlias = Callable[
    [tuple[ToolCall, ...], tuple[Message, ...], "LoopState", RunConfig],
    AsyncIterator[Message],
]


#: Attempt auto-compaction on conversation history if needed or requested.
Compact: TypeAlias = Callable[["LoopState", RunConfig], Awaitable[CompactionResult]]

#: Generate a unique identifier string (e.g. turn_id, call_id).
UuidGenerator: TypeAlias = Callable[[], str]

#: Provide the current timestamp in seconds.
NowProvider: TypeAlias = Callable[[], float]

#: Sink for telemetry, transitions, and the event log (Part L1).
EventSink: TypeAlias = Callable[[Any], None]

#: `QueryDeps.microcompact`: the stored history before the model call, possibly lighter.
Microcompact: TypeAlias = Callable[[tuple[Message, ...], "LoopState", RunConfig], Awaitable[tuple[Message, ...]]]

#: `QueryDeps.autocompact`: `autoCompactIfNeeded` over the history the call will send. The
#: outcome is `app.services.compact.auto_compact.AutoCompactOutcome`.
Autocompact: TypeAlias = Callable[[tuple[Message, ...], "LoopState", RunConfig], Awaitable[Any]]


async def no_microcompact(messages: tuple[Message, ...], state: LoopState, config: RunConfig) -> tuple[Message, ...]:
    """The external build's microcompact: time-based is disabled, cached is feature-gated."""
    return messages


async def no_autocompact(messages: tuple[Message, ...], state: LoopState, config: RunConfig) -> Any:
    """A run that never compacts proactively (fakes, and runs with autocompact off)."""
    from app.services.compact.auto_compact import AutoCompactOutcome

    return AutoCompactOutcome(was_compacted=False)


@dataclass(frozen=True, slots=True)
class LoopDeps:
    """
    Everything the loop reaches the outside world through.

    Widened in Part A4 to the full seam: model calls, tool execution, compaction,
    stop hooks, uuid generation, clock provider, and the event sink.

    Frozen: the seam is chosen once per run. A dependency that could be swapped mid-run
    would make a transcript unexplainable — half the turns answered by one model call and
    half by another, with nothing in the record saying where the switch happened.
    """

    call_model: CallModel
    run_tools: RunTools
    compact: Compact
    stop_hooks: StopHooks
    uuid: UuidGenerator
    now: NowProvider
    emit_event: EventSink
    #: Upstream's own two compaction slots (`query/deps.ts`). Defaults are the no-ops a fake
    #: needs; `get_production_deps` binds the real ones.
    microcompact: Microcompact = no_microcompact
    autocompact: Autocompact = no_autocompact


def production_uuid() -> str:
    return str(uuid.uuid4())


def production_now() -> float:
    return time.time()


def production_emit_event(event: Any) -> None:
    pass  # Extend as necessary for telemetry


def query_source_of(context: Any) -> str:
    """Upstream's `querySource`: a headless main thread is `sdk`, a worker `agent:<type>`."""
    agent_type = getattr(context, "agent_type", None)
    return f"agent:{agent_type}" if getattr(context, "agent_id", None) else "sdk"


def post_compact_attachments(context: Any) -> list[Message]:
    """
    What follows the boundary after any compaction, besides the restored files and the
    SessionStart(`compact`) hooks: the TDD state from the authoritative ledger and the last
    observed `RunTests` run. The summary may say anything; this cannot.
    """
    app_state = context.get_app_state()
    return [
        create_tdd_state_attachment_message(
            app_state.phase_ledger, app_state.last_test_run, after_compaction=True
        )
    ]


async def production_microcompact(
    messages: tuple[Message, ...], state: LoopState, config: RunConfig
) -> tuple[Message, ...]:
    """`microcompactMessages` (time-based path; its external default config is disabled)."""
    is_main_thread = getattr(state.tool_context, "agent_id", None) is None
    return tuple(microcompact_messages(messages, is_main_thread=is_main_thread).messages)


async def production_autocompact(
    messages: tuple[Message, ...], state: LoopState, config: RunConfig
) -> AutoCompactOutcome:
    """`autoCompactIfNeeded` bound to this run's model, tracking and summarizer."""
    return await auto_compact_if_needed(
        messages,
        state.tool_context,
        model=config.model,
        tracking=state.compaction_tracking,
        summarizer=build_compact_summarizer(),
        post_compact_attachments=post_compact_attachments,
        query_source=query_source_of(state.tool_context),
        transcript_path=state.tool_context.transcript_path,
    )


async def production_compact(state: LoopState, config: RunConfig) -> CompactionResult:
    """
    The reactive compaction after a prompt-too-long: `compactConversation` over the whole
    history (upstream's `reactiveCompact` module is absent from the build; its call sites in
    `query.ts` are kept, so the same full compaction answers them). A failure leaves the
    history as it was and reports no compaction, so the engine surfaces the error.
    """
    try:
        result = await compact_conversation(
            state.messages,
            state.tool_context,
            summarizer=build_compact_summarizer(),
            trigger="auto",
            post_compact_attachments=post_compact_attachments,
            model=config.model,
            transcript_path=state.tool_context.transcript_path,
        )
    except Exception:  # noqa: BLE001 - upstream logs a failed compaction and carries on
        return CompactionResult(compacted=False, messages=state.messages, tracking=state.compaction_tracking)
    messages: Sequence[Message] = build_post_compact_messages(result)
    tracking = CompactionTracking(compacted=True, turn_id=production_uuid(), turn_counter=0, consecutive_failures=0)
    return CompactionResult(compacted=True, messages=tuple(messages), tracking=tracking)


def get_production_deps() -> LoopDeps:
    """Builds the real dependencies for the engine."""
    return LoopDeps(
        call_model=stream_call_model,
        run_tools=run_tools,
        compact=production_compact,
        # Barrier 3: `tdd_phase_incomplete_hook` first, then the configured Stop hooks,
        # which can add a block but never clear one (`app/query/stop_hooks.py`).
        stop_hooks=build_stop_hooks_runner(),
        uuid=production_uuid,
        now=production_now,
        emit_event=production_emit_event,
        microcompact=production_microcompact,
        autocompact=production_autocompact,
    )
