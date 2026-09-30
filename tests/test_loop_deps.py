"""
Unit tests for the LoopDeps dependency injection seam and result structures (Part A4).
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.config import build_run_config
from app.loop.context import AppStateStore, tool_context_for
from app.loop.deps import (
    CompactionResult,
    LoopDeps,
    StopHookResult,
)
from app.loop.messages import Message, ToolCall
from app.loop.state import CompactionTracking, LoopState, initial_loop_state

CONFIG = build_run_config("test-run-a4", postgres_checkpointing=False)


def sample_state() -> LoopState:
    return initial_loop_state(
        (HumanMessage(content="test spec"),),
        tool_context_for(AppStateStore()),
    )


class TestStopHookResult:
    def test_default_values(self) -> None:
        result = StopHookResult()
        assert result.blocking_errors == ()
        assert result.prevent_continuation is False

    def test_blocking_errors_preserved_when_continuation_allowed(self) -> None:
        err = HumanMessage(content="cycle incomplete: test never failed")
        result = StopHookResult(blocking_errors=(err,), prevent_continuation=False)
        assert result.blocking_errors == (err,)
        assert result.prevent_continuation is False

    def test_precedence_prevent_continuation_blanks_blocking_errors(self) -> None:
        """
        §3.3 precedence rule: preventContinuation wins over blockingErrors and blanks them.
        A hook cannot both stop the turn and request a retry.
        """
        err = HumanMessage(content="should be blanked")
        result = StopHookResult(blocking_errors=(err,), prevent_continuation=True)
        assert result.blocking_errors == ()
        assert result.prevent_continuation is True

    def test_prevent_continuation_with_empty_errors(self) -> None:
        result = StopHookResult(blocking_errors=(), prevent_continuation=True)
        assert result.blocking_errors == ()
        assert result.prevent_continuation is True

    def test_stop_hook_result_is_frozen(self) -> None:
        result = StopHookResult()
        with pytest.raises(FrozenInstanceError):
            result.prevent_continuation = True  # type: ignore[misc]


class TestCompactionResult:
    def test_compaction_result_attributes(self) -> None:
        msg = HumanMessage(content="compacted summary")
        tracking = CompactionTracking(compacted=True, turn_id="turn-1", turn_counter=1)
        result = CompactionResult(compacted=True, messages=(msg,), tracking=tracking)
        assert result.compacted is True
        assert result.messages == (msg,)
        assert result.tracking == tracking

    def test_compaction_result_without_tracking(self) -> None:
        result = CompactionResult(compacted=False, messages=())
        assert result.compacted is False
        assert result.messages == ()
        assert result.tracking is None

    def test_compaction_result_is_frozen(self) -> None:
        result = CompactionResult(compacted=False, messages=())
        with pytest.raises(FrozenInstanceError):
            result.compacted = True  # type: ignore[misc]


class TestLoopDeps:
    def test_loop_deps_holds_all_seven_seam_components(self) -> None:
        field_names = [f.name for f in fields(LoopDeps)]
        expected = [
            "call_model",
            "run_tools",
            "compact",
            "stop_hooks",
            "uuid",
            "now",
            "emit_event",
        ]
        assert field_names == expected

    def test_loop_deps_is_frozen(self) -> None:
        async def fake_model(state: LoopState, config: Any) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")

        async def fake_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], state: LoopState, config: Any
        ) -> AsyncIterator[Message]:
            if False:
                yield ToolMessage(content="", tool_call_id="")

        async def fake_compact(state: LoopState, config: Any) -> CompactionResult:
            return CompactionResult(compacted=False, messages=state.messages)

        async def fake_stop_hooks(state: LoopState, config: Any) -> StopHookResult:
            return StopHookResult()

        deps = LoopDeps(
            call_model=fake_model,
            run_tools=fake_tools,
            compact=fake_compact,
            stop_hooks=fake_stop_hooks,
            uuid=lambda: "id-1",
            now=lambda: 123.45,
            emit_event=lambda e: None,
        )

        with pytest.raises(FrozenInstanceError):
            deps.uuid = lambda: "id-2"  # type: ignore[misc]

    def test_injected_seams_are_callable_and_behave_as_expected(self) -> None:
        events_emitted: list[Any] = []

        async def model_stub(state: LoopState, config: Any) -> AsyncIterator[Message]:
            yield AIMessage(content="response")

        async def tools_stub(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], state: LoopState, config: Any
        ) -> AsyncIterator[Message]:
            yield ToolMessage(content="tool output", tool_call_id=calls[0]["id"])

        async def compact_stub(state: LoopState, config: Any) -> CompactionResult:
            return CompactionResult(compacted=True, messages=state.messages)

        async def stop_hooks_stub(state: LoopState, config: Any) -> StopHookResult:
            return StopHookResult(prevent_continuation=False)

        deps = LoopDeps(
            call_model=model_stub,
            run_tools=tools_stub,
            compact=compact_stub,
            stop_hooks=stop_hooks_stub,
            uuid=lambda: "fixed-uuid-123",
            now=lambda: 1700000000.0,
            emit_event=events_emitted.append,
        )

        state = sample_state()

        async def run_checks() -> None:
            # Check model seam
            msgs = [m async for m in deps.call_model(state, CONFIG)]
            assert len(msgs) == 1
            assert msgs[0].content == "response"

            # Check tools seam
            tool_calls: tuple[ToolCall, ...] = ({"name": "test_tool", "args": {}, "id": "call-1"},)
            tool_res = [t async for t in deps.run_tools(tool_calls, (msgs[0],), state, CONFIG)]
            assert len(tool_res) == 1
            assert tool_res[0].content == "tool output"

            # Check compact seam
            c_res = await deps.compact(state, CONFIG)
            assert c_res.compacted is True

            # Check stop_hooks seam
            s_res = await deps.stop_hooks(state, CONFIG)
            assert s_res.prevent_continuation is False

        asyncio.run(run_checks())

        # Check platform seams
        assert deps.uuid() == "fixed-uuid-123"
        assert deps.now() == 1700000000.0

        # Check event sink seam
        deps.emit_event({"event": "turn_started"})
        assert events_emitted == [{"event": "turn_started"}]
