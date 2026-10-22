"""
The production dependencies of the loop (`query/deps.ts` -> `productionDeps`).

Phase 5 binds the compaction slots: `microcompact` (the external build's time-based path,
off by default), `autocompact` (`autoCompactIfNeeded`), and `compact`, the reactive
compaction the engine runs after a prompt-too-long. Both compactions inject the same
post-compact attachments: the **TDD state re-attached from the ledger and the last
`RunTests` record**, never from the summary.
"""

import time
import uuid
from collections.abc import Sequence
from typing import Any

from app.loop.attachments import create_tdd_state_attachment_message
from app.loop.compact.auto_compact import AutoCompactOutcome, auto_compact_if_needed
from app.loop.compact.compact import build_post_compact_messages, compact_conversation
from app.loop.compact.micro_compact import microcompact_messages
from app.loop.compact.summarizer import build_compact_summarizer
from app.loop.config import RunConfig
from app.loop.deps import CompactionResult, LoopDeps
from app.loop.hooks.stop_hooks import build_stop_hooks_runner
from app.loop.messages import Message
from app.loop.model import stream_call_model
from app.loop.state import CompactionTracking, LoopState
from app.loop.tools.orchestration import run_tools


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
        # which can add a block but never clear one (`app/loop/hooks/stop_hooks.py`).
        stop_hooks=build_stop_hooks_runner(),
        uuid=production_uuid,
        now=production_now,
        emit_event=production_emit_event,
        microcompact=production_microcompact,
        autocompact=production_autocompact,
    )
