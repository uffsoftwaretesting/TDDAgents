"""
Unit tests for withhold-then-decide, recovery continue sites, and stream abortion (Parts F1, F5, F6).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.config import RunConfig, build_run_config
from app.loop.context import AppStateStore, ToolContext, tool_context_for
from app.loop.deps import CompactionResult, LoopDeps
from app.loop.engine import run_loop
from app.loop.messages import Message, ToolCall
from app.loop.state import LoopState, initial_loop_state
from app.loop.streaming.withhold import (
    WithholdGateSnapshot,
    _get_api_error,
    is_max_output_tokens,
    is_media_size_error,
    is_prompt_too_long,
    is_withheld_error,
    take_withhold_gate_snapshot,
)
from app.loop.tools.base import build_tool
from app.loop.tools.types import ToolResult
from app.loop.transitions import Continue, Terminal, Terminated, Transition

CONFIG = build_run_config("recovery-test", postgres_checkpointing=False)


def make_deps(
    model_turns: list[list[Message]],
    compact_res: CompactionResult | None = None,
) -> LoopDeps:
    turns = list(model_turns)

    async def call_model(state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
        if not turns:
            raise AssertionError("Model called more times than scripted")
        for m in turns.pop(0):
            yield m

    async def compact(state: LoopState, config: RunConfig) -> CompactionResult:
        if compact_res is not None:
            return compact_res
        return CompactionResult(compacted=False, messages=state.messages)

    async def stop_hooks(state: LoopState, config: RunConfig) -> Any:
        return SimpleNamespace(blocking_errors=(), prevent_continuation=False)

    async def run_tools(
        calls: tuple[ToolCall, ...],
        asst: tuple[Message, ...],
        state: LoopState,
        config: RunConfig,
    ) -> AsyncIterator[Message]:
        for c in calls:
            cid = str(c.get("id") or "")
            yield ToolMessage(content="ok", tool_call_id=cid)

    return LoopDeps(
        call_model=call_model,
        run_tools=run_tools,
        compact=compact,
        stop_hooks=stop_hooks,
        uuid=lambda: "uid",
        now=lambda: 100.0,
        emit_event=lambda e: None,
    )


def make_snapshot(
    reactive_compact_enabled: bool = True,
    context_collapse_enabled: bool = False,
    media_recovery_enabled: bool = False,
    max_output_tokens_recovery_enabled: bool = True,
    max_output_tokens_escalate_enabled: bool = False,
) -> WithholdGateSnapshot:
    return WithholdGateSnapshot(
        reactive_compact_enabled=reactive_compact_enabled,
        context_collapse_enabled=context_collapse_enabled,
        media_recovery_enabled=media_recovery_enabled,
        max_output_tokens_recovery_enabled=max_output_tokens_recovery_enabled,
        max_output_tokens_escalate_enabled=max_output_tokens_escalate_enabled,
    )


class TestWithholdPredicates:
    def test_predicates_on_bare_object(self) -> None:
        bare = object()
        assert _get_api_error(bare) is None  # type: ignore
        assert is_prompt_too_long(bare) is False  # type: ignore
        assert is_max_output_tokens(bare) is False  # type: ignore
        assert is_media_size_error(bare) is False  # type: ignore

    def test_get_api_error_branches(self) -> None:
        m_attr = SimpleNamespace(api_error="custom_err")
        assert _get_api_error(m_attr) == "custom_err"  # type: ignore

        m_dict = AIMessage(content="", additional_kwargs={"api_error": "dict_err"})
        assert _get_api_error(m_dict) == "dict_err"

        m_none = AIMessage(content="")
        assert _get_api_error(m_none) is None

        m_non_dict = SimpleNamespace(api_error=None, additional_kwargs=None)
        assert _get_api_error(m_non_dict) is None  # type: ignore

        m_dict_non_str = AIMessage(content="", additional_kwargs={"api_error": 123})
        assert _get_api_error(m_dict_non_str) is None

    def test_is_prompt_too_long_variations(self) -> None:
        m1 = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        assert is_prompt_too_long(m1) is True

        m_cle = AIMessage(content="", additional_kwargs={"api_error": "context_length_exceeded"})
        assert is_prompt_too_long(m_cle) is True

        m_direct_status = SimpleNamespace(status_code=413, content="")
        assert is_prompt_too_long(m_direct_status) is True  # type: ignore

        m2 = AIMessage(content="", additional_kwargs={"status_code": 413})
        assert is_prompt_too_long(m2) is True

        m3 = AIMessage(content="Error: prompt is too long for this model")
        assert is_prompt_too_long(m3) is True

        m4 = AIMessage(content="Exceeded maximum context length allowed")
        assert is_prompt_too_long(m4) is True

        m5 = AIMessage(content="Request exceeds token limit")
        assert is_prompt_too_long(m5) is True

        m_ok = AIMessage(content="Normal response")
        assert is_prompt_too_long(m_ok) is False

    def test_is_max_output_tokens_variations(self) -> None:
        m1 = AIMessage(content="cut", additional_kwargs={"api_error": "max_output_tokens"})
        assert is_max_output_tokens(m1) is True

        m2_len = AIMessage(content="cut", additional_kwargs={"finish_reason": "length"})
        assert is_max_output_tokens(m2_len) is True

        m2_tok = AIMessage(content="cut", additional_kwargs={"finish_reason": "max_tokens"})
        assert is_max_output_tokens(m2_tok) is True

        m3_len = AIMessage(content="cut", response_metadata={"finish_reason": "length"})
        assert is_max_output_tokens(m3_len) is True

        m3_tok = AIMessage(content="cut", response_metadata={"finish_reason": "max_tokens"})
        assert is_max_output_tokens(m3_tok) is True

        m_ok = AIMessage(content="full answer", additional_kwargs={"finish_reason": "stop"})
        assert is_max_output_tokens(m_ok) is False

    def test_is_media_size_error_variations(self) -> None:
        m_img = AIMessage(content="", additional_kwargs={"api_error": "image_error"})
        assert is_media_size_error(m_img) is True

        m_media = AIMessage(content="", additional_kwargs={"api_error": "media_size_error"})
        assert is_media_size_error(m_media) is True

        m_large = AIMessage(content="", additional_kwargs={"api_error": "media_too_large"})
        assert is_media_size_error(m_large) is True

        m_content = AIMessage(content="Error: image exceeds 5MB")
        assert is_media_size_error(m_content) is True

        m_ok = AIMessage(content="Valid text")
        assert is_media_size_error(m_ok) is False


class TestWithholdGateSnapshot:
    def test_snapshot_respects_attempted_reactive_compact(self) -> None:
        context = tool_context_for(AppStateStore())
        state_first = initial_loop_state(messages=(), tool_context=context)
        gate_first = take_withhold_gate_snapshot(state_first, CONFIG)
        assert gate_first.reactive_compact_enabled is True
        assert gate_first.context_collapse_enabled is False
        assert gate_first.media_recovery_enabled is False
        assert gate_first.max_output_tokens_recovery_enabled is True
        assert gate_first.max_output_tokens_escalate_enabled is False

        state_retry = LoopState(
            messages=(),
            tool_context=context,
            phase_ledger=state_first.phase_ledger,
            compaction_tracking=None,
            has_attempted_reactive_compact=True,
            stop_hook_active=None,
            turn_count=1,
            transition=Transition(reason=Continue.REACTIVE_COMPACT_RETRY),
        )
        gate_retry = take_withhold_gate_snapshot(state_retry, CONFIG)
        assert gate_retry.reactive_compact_enabled is False
        assert gate_retry.context_collapse_enabled is False
        assert gate_retry.media_recovery_enabled is False
        assert gate_retry.max_output_tokens_recovery_enabled is True
        assert gate_retry.max_output_tokens_escalate_enabled is False

    def test_is_withheld_error_reads_snapshot_faithfully(self) -> None:
        """
        Invariant F5: Withhold decisions are strictly conditioned on the gate snapshot.
        """
        ptl = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        media = AIMessage(content="", additional_kwargs={"api_error": "image_error"})
        max_tok = AIMessage(content="cut", additional_kwargs={"finish_reason": "length"})
        normal = AIMessage(content="hello")

        # PTL with collapse enabled
        gate_collapse = make_snapshot(reactive_compact_enabled=False, context_collapse_enabled=True)
        assert is_withheld_error(ptl, gate_collapse) is True

        # PTL with reactive compact enabled
        gate_compact = make_snapshot(reactive_compact_enabled=True, context_collapse_enabled=False)
        assert is_withheld_error(ptl, gate_compact) is True

        # PTL with both disabled
        gate_none = make_snapshot(
            reactive_compact_enabled=False,
            context_collapse_enabled=False,
            media_recovery_enabled=False,
            max_output_tokens_recovery_enabled=False,
            max_output_tokens_escalate_enabled=False,
        )
        assert is_withheld_error(ptl, gate_none) is False

        # Media error with media recovery enabled/disabled
        gate_media_on = make_snapshot(media_recovery_enabled=True)
        assert is_withheld_error(media, gate_media_on) is True
        assert is_withheld_error(media, gate_none) is False

        # Max output tokens with escalate or recovery
        gate_escalate = make_snapshot(
            max_output_tokens_recovery_enabled=False,
            max_output_tokens_escalate_enabled=True,
        )
        assert is_withheld_error(max_tok, gate_escalate) is True
        assert is_withheld_error(max_tok, gate_none) is False

        # Normal message never withheld
        assert is_withheld_error(normal, make_snapshot()) is False


def test_withheld_413_recovers_via_reactive_compact() -> None:
    """
    Invariant F5 & F6: 413 is withheld from caller emission, reactive compact runs,
    and loop transitions to REACTIVE_COMPACT_RETRY.
    """
    async def _run() -> None:
        context = tool_context_for(AppStateStore())
        state = initial_loop_state(messages=(HumanMessage(content="task"),), tool_context=context)

        ptl_msg = AIMessage(content="cut", additional_kwargs={"api_error": "prompt_too_long"})
        ok_msg = AIMessage(content="recovered answer")

        compact_messages = (HumanMessage(content="compacted task"),)
        deps = make_deps(
            model_turns=[[ptl_msg], [ok_msg]],
            compact_res=CompactionResult(compacted=True, messages=compact_messages),
        )

        emitted_events: list[Any] = []
        async for event in run_loop(state, CONFIG, deps):
            emitted_events.append(event)

        # Invariant F5: The 413 message was WITHHELD from caller emission!
        assert ptl_msg not in emitted_events
        # Only the recovered answer and final Terminated are emitted to caller
        assert ok_msg in emitted_events
        assert any(isinstance(e, Terminated) and e.reason is Terminal.COMPLETED for e in emitted_events)

    asyncio.run(_run())


def test_exhausted_413_surfaces_withheld_error() -> None:
    """
    Invariant F5: If reactive compact fails or has already been attempted,
    the withheld error surfaces and loop terminates with PROMPT_TOO_LONG.
    """
    async def _run() -> None:
        context = tool_context_for(AppStateStore())
        state = LoopState(
            messages=(HumanMessage(content="task"),),
            tool_context=context,
            phase_ledger=context.get_app_state().phase_ledger,
            compaction_tracking=None,
            has_attempted_reactive_compact=True,  # Already attempted!
            stop_hook_active=None,
            turn_count=1,
            transition=Transition(reason=Continue.REACTIVE_COMPACT_RETRY),
        )

        ptl_msg = AIMessage(content="cut", additional_kwargs={"api_error": "prompt_too_long"})
        deps = make_deps(model_turns=[[ptl_msg]])

        emitted_events: list[Any] = []
        async for event in run_loop(state, CONFIG, deps):
            emitted_events.append(event)

        # When recovery cannot run, withheld error surfaces
        assert ptl_msg in emitted_events
        term = [e for e in emitted_events if isinstance(e, Terminated)][0]
        assert term.reason is Terminal.PROMPT_TOO_LONG

    asyncio.run(_run())


def test_withheld_max_output_tokens_recovers() -> None:
    """
    Invariant F6: max_output_tokens is withheld, resume prompt is injected,
    and loop continues with MAX_OUTPUT_TOKENS_RECOVERY.
    """
    async def _run() -> None:
        context = tool_context_for(AppStateStore())
        state = initial_loop_state(messages=(HumanMessage(content="write function"),), tool_context=context)

        truncated_msg = AIMessage(content="def foo():\n    return", additional_kwargs={"finish_reason": "length"})
        resumed_msg = AIMessage(content=" 42")

        deps = make_deps(model_turns=[[truncated_msg], [resumed_msg]])

        emitted_events: list[Any] = []
        async for event in run_loop(state, CONFIG, deps):
            emitted_events.append(event)

        # Truncated message is withheld during turn 1
        assert truncated_msg not in emitted_events
        assert resumed_msg in emitted_events
        term = [e for e in emitted_events if isinstance(e, Terminated)][0]
        assert term.reason is Terminal.COMPLETED

    asyncio.run(_run())


def test_eager_dispatch_at_content_block_close() -> None:
    """
    Invariant F2: Tool call is dispatched at content-block close while stream continues.
    """
    async def _run() -> None:
        tool_executed = asyncio.Event()

        async def call_echo(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            tool_executed.set()
            return ToolResult(content="tool output", tool_use_id="t1")

        echo_tool = build_tool(
            name="Echo",
            prompt="Echo tool",
            call=call_echo,
            is_concurrency_safe=lambda args: True,
        )

        store = AppStateStore()
        context = tool_context_for(store, tools=(echo_tool,))
        state = initial_loop_state(messages=(HumanMessage(content="run"),), tool_context=context)

        c1: ToolCall = {"id": "t1", "name": "Echo", "args": {}}
        chunk1 = AIMessage(content="", tool_calls=[c1])
        chunk2 = AIMessage(content="closing text")

        deps = make_deps(model_turns=[[chunk1, chunk2]])

        emitted_events: list[Any] = []
        async for event in run_loop(state, CONFIG, deps):
            emitted_events.append(event)

        assert tool_executed.is_set()
        tool_results = [e for e in emitted_events if isinstance(e, ToolMessage)]
        assert len(tool_results) == 1
        assert tool_results[0].content == "tool output"

    asyncio.run(_run())
