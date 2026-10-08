"""
The loop engine tests (Parts A1–A7), driven entirely by fakes.

No sandbox, no network, no model. The fakes are also the instrumentation: `FakeModel`
records the `LoopState` it was handed on each iteration, which is how these tests observe
the reset/preserve decisions without the loop having to expose its state.

`asyncio.run` per test rather than a plugin: the repo has no async test dependency and
needs none for this.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.config import RunConfig, build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import (
    CallModel,
    Compact,
    CompactionResult,
    EventSink,
    LoopDeps,
    NowProvider,
    RunTools,
    StopHookResult,
    StopHooks,
    UuidGenerator,
)
from app.loop.engine import (
    BlockingLimitError,
    LoopEvent,
    LoopNeverTerminated,
    ModelCallError,
    PromptTooLongError,
    _has_hook_stopped,
    _is_api_error,
    drain,
    run_loop,
)
from app.loop.ledger import PhaseLedger
from app.loop.messages import Message, ToolCall
from app.loop.state import CompactionTracking, LoopState, initial_loop_state
from app.loop.transitions import Continue, Terminal, Terminated, Transition

CONFIG = build_run_config("tdd-test", postgres_checkpointing=False)


def asks_for(tool: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": tool, "args": {}, "id": call_id}]
    )


class FakeModel:
    """
    Replays a script of turns, and records the state it was called with each time.

    Each element of `turns` is the messages one model call streams back. Running out of
    script is a test bug, not a model behaviour, so it fails loudly.
    """

    def __init__(self, turns: list[list[Message]]) -> None:
        self.turns = turns
        self.seen: list[LoopState] = []
        self.seen_configs: list[RunConfig] = []

    async def __call__(self, state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
        self.seen.append(state)
        self.seen_configs.append(config)
        if not self.turns:
            raise AssertionError("the loop called the model more times than scripted")
        for message in self.turns.pop(0):
            yield message


class FakeTools:
    """Answers every call with one result, and records what it was asked to run."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.assistant_messages: list[tuple[Message, ...]] = []
        self.seen: list[LoopState] = []
        self.seen_configs: list[RunConfig] = []

    async def __call__(
        self,
        tool_calls: tuple[ToolCall, ...],
        assistant_messages: tuple[Message, ...],
        state: LoopState,
        config: RunConfig,
    ) -> AsyncIterator[Message]:
        self.calls.append(tuple(c["name"] for c in tool_calls))
        self.assistant_messages.append(assistant_messages)
        self.seen.append(state)
        self.seen_configs.append(config)
        for call in tool_calls:
            yield ToolMessage(content=f"ran {call['name']}", tool_call_id=call["id"])


class FakeStopHooks:
    """Replays configured stop hook outcomes and records what it was called with."""

    def __init__(self, results: list[StopHookResult] | None = None) -> None:
        self.results = list(results) if results is not None else []
        self.seen: list[LoopState] = []
        self.seen_configs: list[RunConfig] = []

    async def __call__(self, state: LoopState, config: RunConfig) -> StopHookResult:
        self.seen.append(state)
        self.seen_configs.append(config)
        if self.results:
            return self.results.pop(0)
        return StopHookResult()


class FakeCompact:
    """Replays compaction outcomes and records what it was called with."""

    def __init__(self, results: list[CompactionResult] | None = None) -> None:
        self.results = list(results) if results is not None else []
        self.seen: list[LoopState] = []
        self.seen_configs: list[RunConfig] = []

    async def __call__(self, state: LoopState, config: RunConfig) -> CompactionResult:
        self.seen.append(state)
        self.seen_configs.append(config)
        if self.results:
            return self.results.pop(0)
        return CompactionResult(compacted=False, messages=state.messages)


def start(**overrides: Any) -> LoopState:
    """
    A starting state. A `phase_ledger` override is seeded through the app-state store,
    the only place a run's ledger may be set (RunTests is the only later writer).
    """
    ledger = overrides.pop("phase_ledger", None)
    store = AppStateStore(AppState(phase_ledger=ledger)) if ledger is not None else AppStateStore()
    state = initial_loop_state((HumanMessage(content="spec"),), tool_context_for(store))
    return replace(state, **overrides) if overrides else state


def _strict_emit_event(event: Any) -> None:
    if event is None:
        raise ValueError("emitted event cannot be None")
    if not isinstance(event, (Transition, Terminated)):
        raise TypeError(f"unexpected event type: {type(event)}")


def make_fake_deps(
    model: FakeModel | CallModel,
    tools: FakeTools | RunTools | None = None,
    compact: Compact | None = None,
    stop_hooks: StopHooks | None = None,
    uuid: UuidGenerator | None = None,
    now: NowProvider | None = None,
    emit_event: EventSink | None = None,
) -> LoopDeps:
    return LoopDeps(
        call_model=model,
        run_tools=tools if tools is not None else FakeTools(),
        compact=compact if compact is not None else FakeCompact(),
        stop_hooks=stop_hooks if stop_hooks is not None else FakeStopHooks(),
        uuid=uuid if uuid is not None else (lambda: "fake-uuid-1"),
        now=now if now is not None else (lambda: 1000.0),
        emit_event=emit_event if emit_event is not None else _strict_emit_event,
    )


def collect(
    model: FakeModel | CallModel,
    tools: FakeTools | RunTools | None = None,
    state: LoopState | None = None,
    deps: LoopDeps | None = None,
) -> list[LoopEvent]:
    actual_state = state if state is not None else start()
    actual_deps = deps if deps is not None else make_fake_deps(model, tools)

    async def go() -> list[LoopEvent]:
        return [event async for event in run_loop(actual_state, CONFIG, actual_deps)]

    return asyncio.run(go())


class TestTheModelAnswers:
    def test_a_text_answer_ends_the_run(self):
        events = collect(FakeModel([[AIMessage(content="done")]]), FakeTools(), start())
        assert events[-1] == Terminated(reason=Terminal.COMPLETED)

    def test_no_tool_runs_when_the_model_asks_for_none(self):
        tools = FakeTools()
        collect(FakeModel([[AIMessage(content="done")]]), tools, start())
        assert tools.calls == []

    def test_every_message_the_model_produced_is_emitted_in_order(self):
        first, second = AIMessage(content="thinking"), AIMessage(content="done")
        events = collect(FakeModel([[first, second]]), FakeTools(), start())
        assert events == [first, second, Terminated(reason=Terminal.COMPLETED)]

    def test_the_loop_stops_at_the_terminal_event(self):
        model = FakeModel([[AIMessage(content="done")]])
        collect(model, FakeTools(), start())
        assert len(model.seen) == 1


class TestTheModelAsksForTools:
    def test_tools_run_and_the_loop_goes_round_again(self):
        tools = FakeTools()
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        collect(model, tools, start())
        assert tools.calls == [("RunTests",)]
        assert len(model.seen) == 2

    def test_results_are_emitted_between_the_two_model_turns(self):
        asked = asks_for("RunTests", "c1")
        answered = AIMessage(content="done")
        events = collect(FakeModel([[asked], [answered]]), FakeTools(), start())
        assert events[0] is asked
        assert isinstance(events[1], ToolMessage)
        assert events[2] is answered
        assert events[3] == Terminated(reason=Terminal.COMPLETED)

    def test_calls_from_several_assistant_messages_are_collected_together(self):
        model = FakeModel(
            [
                [asks_for("ReadFile", "c1"), AIMessage(content="and"), asks_for("RunTests", "c2")],
                [AIMessage(content="done")],
            ]
        )
        tools = FakeTools()
        collect(model, tools, start())
        assert tools.calls == [("ReadFile", "RunTests")]

    def test_the_tool_runner_is_told_which_messages_asked(self):
        asked = asks_for("RunTests", "c1")
        tools = FakeTools()
        collect(FakeModel([[asked], [AIMessage(content="done")]]), tools, start())
        assert tools.assistant_messages == [(asked,)]

    def test_several_rounds_run_until_the_model_stops_asking(self):
        tools = FakeTools()
        model = FakeModel(
            [
                [asks_for("RunTests", "c1")],
                [asks_for("WriteFile", "c2")],
                [AIMessage(content="done")],
            ]
        )
        collect(model, tools, start())
        assert tools.calls == [("RunTests",), ("WriteFile",)]
        assert [s.turn_count for s in model.seen] == [1, 2, 3]


class TestTheNextTurnSite:
    """One assertion per field of the record, which is what A7 will generalise."""

    def two_turns(self, **overrides: Any) -> FakeModel:
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        collect(model, FakeTools(), start(**overrides))
        return model

    def test_turn_count_advances(self):
        assert [s.turn_count for s in self.two_turns().seen] == [1, 2]

    def test_the_transition_records_why(self):
        seen = self.two_turns().seen
        assert seen[0].transition is None
        assert seen[1].transition is not None
        assert seen[1].transition.reason is Continue.NEXT_TURN

    def test_the_conversation_grows_by_the_answer_and_the_results(self):
        seen = self.two_turns().seen
        assert len(seen[1].messages) == len(seen[0].messages) + 2
        assert seen[1].messages[: len(seen[0].messages)] == seen[0].messages
        assert isinstance(seen[1].messages[-1], ToolMessage)

    def test_the_reactive_compact_guard_is_cleared(self):
        """A round of tool calls may have changed the context enough to retry."""
        seen = self.two_turns(has_attempted_reactive_compact=True).seen
        assert seen[0].has_attempted_reactive_compact is True
        assert seen[1].has_attempted_reactive_compact is False

    def test_the_stop_hook_latch_is_preserved(self):
        """Running tools does not answer whether a hook already blocked this turn."""
        assert self.two_turns(stop_hook_active=True).seen[1].stop_hook_active is True

    def test_the_phase_ledger_is_preserved(self):
        ledger = PhaseLedger(phase="GREEN", red_confirmed=True)
        assert self.two_turns(phase_ledger=ledger).seen[1].phase_ledger == ledger

    def test_compaction_tracking_is_preserved(self):
        tracking = CompactionTracking(compacted=True, turn_id="t1", turn_counter=2)
        assert self.two_turns(compaction_tracking=tracking).seen[1].compaction_tracking == tracking

    def test_the_state_is_replaced_rather_than_edited(self):
        seen = self.two_turns().seen
        assert seen[1] is not seen[0]


class TestTheContextIsRespreadEachIteration:
    def test_a_tool_sees_the_conversation_as_it_stands_now(self):
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        collect(model, FakeTools(), start())
        for state in model.seen:
            assert state.tool_context.messages == state.messages

    def test_the_first_iteration_already_sees_it(self):
        """
        `initial_loop_state` builds the context before the conversation exists, so an
        iteration that did not re-spread would hand the first tool an empty history.
        """
        model = FakeModel([[AIMessage(content="done")]])
        state = start()
        assert state.tool_context.messages == ()
        collect(model, FakeTools(), state)
        assert model.seen[0].tool_context.messages == state.messages


class TestWhatTheSeamIsHanded:
    """
    All seam dependencies are handed the run's config and the state of the iteration
    calling them.
    """

    def run(self) -> tuple[FakeModel, FakeTools]:
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        tools = FakeTools()
        collect(model, tools, start())
        return model, tools

    def test_the_model_is_given_the_run_config(self):
        model, _ = self.run()
        assert model.seen_configs == [CONFIG, CONFIG]

    def test_the_tool_runner_is_given_the_run_config(self):
        _, tools = self.run()
        assert tools.seen_configs == [CONFIG]

    def test_the_tool_runner_is_given_the_state_of_the_iteration_that_called_it(self):
        model, tools = self.run()
        assert tools.seen == [model.seen[0]]

    def test_stop_hooks_are_given_the_run_config_and_state(self):
        stop_hooks = FakeStopHooks([StopHookResult()])
        model = FakeModel([[AIMessage(content="done")]])
        deps = make_fake_deps(model, stop_hooks=stop_hooks)
        collect(model, deps=deps)
        assert stop_hooks.seen_configs == [CONFIG]
        assert len(stop_hooks.seen) == 1
        assert stop_hooks.seen[0] == model.seen[0]

    def test_compact_is_given_the_run_config_and_state(self):
        compact = FakeCompact([CompactionResult(compacted=True, messages=())])
        error_msg = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        model = FakeModel([[error_msg], [AIMessage(content="done")]])
        deps = make_fake_deps(model, compact=compact)
        collect(model, deps=deps)
        assert compact.seen_configs == [CONFIG]
        assert len(compact.seen) == 1

    def test_event_sink_receives_transitions_and_termination(self):
        events_emitted: list[Any] = []
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        deps = make_fake_deps(model, emit_event=events_emitted.append)
        collect(model, deps=deps)
        assert len(events_emitted) == 2
        assert events_emitted[0].reason is Continue.NEXT_TURN
        assert events_emitted[1].reason is Terminal.COMPLETED


class TestAllTerminalReturns:
    """Part A5: the remaining terminal returns, each with its own test."""

    def test_terminal_completed(self):
        model = FakeModel([[AIMessage(content="plain text answer")]])
        events = collect(model)
        assert events[-1] == Terminated(reason=Terminal.COMPLETED)

    def test_terminal_stop_hook_prevented(self):
        stop_hooks = FakeStopHooks([StopHookResult(prevent_continuation=True)])
        model = FakeModel([[AIMessage(content="done")]])
        deps = make_fake_deps(model, stop_hooks=stop_hooks)
        events = collect(model, deps=deps)
        assert events[-1] == Terminated(reason=Terminal.STOP_HOOK_PREVENTED)

    def test_terminal_aborted_streaming_at_turn_start(self):
        state = start()
        state.tool_context.cancel.cancel()
        model = FakeModel([[AIMessage(content="never reached")]])
        events = collect(model, state=state)
        assert events == [Terminated(reason=Terminal.ABORTED_STREAMING)]
        assert model.seen == []

    def test_terminal_aborted_streaming_during_call(self):
        state = start()

        async def cancelling_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            yield AIMessage(content="streaming...")
            state.tool_context.cancel.cancel()

        events = collect(cancelling_model, state=state)
        assert events[-1] == Terminated(reason=Terminal.ABORTED_STREAMING)

    def test_terminal_aborted_tools(self):
        state = start()

        async def cancelling_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig
        ) -> AsyncIterator[Message]:
            yield ToolMessage(content="ran", tool_call_id=calls[0]["id"])
            state.tool_context.cancel.cancel()

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=cancelling_tools, state=state)
        assert events[-1] == Terminated(reason=Terminal.ABORTED_TOOLS)

    def test_terminal_hook_stopped_via_kwargs(self):
        async def hook_stopping_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig
        ) -> AsyncIterator[Message]:
            msg = ToolMessage(
                content="vetoed",
                tool_call_id=calls[0]["id"],
                additional_kwargs={"hook_stopped_continuation": True},
            )
            yield msg

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=hook_stopping_tools)
        assert events[-1] == Terminated(reason=Terminal.HOOK_STOPPED)

    def test_terminal_hook_stopped_via_attribute(self):
        async def hook_stopping_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig
        ) -> AsyncIterator[Message]:
            msg = SimpleNamespace(
                content="vetoed",
                tool_call_id=calls[0]["id"],
                hook_stopped_continuation=True,
                additional_kwargs={},
            )
            yield msg  # type: ignore[misc]

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=hook_stopping_tools)
        assert events[-1] == Terminated(reason=Terminal.HOOK_STOPPED)

    def test_terminal_model_error_on_exception(self):
        async def failing_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")
            raise RuntimeError("API failure")

        events = collect(failing_model)
        assert events == [Terminated(reason=Terminal.MODEL_ERROR)]

    def test_terminal_model_call_error(self):
        async def failing_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")
            raise ModelCallError("model provider failed")

        events = collect(failing_model)
        assert events == [Terminated(reason=Terminal.MODEL_ERROR)]

    def test_terminal_blocking_limit_exception(self):
        async def blocking_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")
            raise BlockingLimitError("context window exceeded hard limit")

        events = collect(blocking_model)
        assert events == [Terminated(reason=Terminal.BLOCKING_LIMIT)]

    def test_terminal_blocking_limit_message(self):
        msg = AIMessage(content="", additional_kwargs={"api_error": "blocking_limit"})
        model = FakeModel([[msg]])
        events = collect(model)
        assert events[-1] == Terminated(reason=Terminal.BLOCKING_LIMIT)

    def test_terminal_blocking_limit_attribute(self):
        msg = SimpleNamespace(content="", api_error="blocking_limit", additional_kwargs={})
        model = FakeModel([[msg]])  # type: ignore[list-item]
        events = collect(model)
        assert events[-1] == Terminated(reason=Terminal.BLOCKING_LIMIT)

    def test_terminal_prompt_too_long_when_already_compacted(self):
        msg = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        model = FakeModel([[msg]])
        state = start(has_attempted_reactive_compact=True)
        events = collect(model, state=state)
        assert events[-1] == Terminated(reason=Terminal.PROMPT_TOO_LONG)

    def test_terminal_prompt_too_long_when_compaction_fails(self):
        compact = FakeCompact([CompactionResult(compacted=False, messages=())])
        msg = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        model = FakeModel([[msg]])
        deps = make_fake_deps(model, compact=compact)
        events = collect(model, deps=deps)
        assert events[-1] == Terminated(reason=Terminal.PROMPT_TOO_LONG)

    def test_terminal_prompt_too_long_exception(self):
        compact = FakeCompact([CompactionResult(compacted=False, messages=())])

        async def prompt_too_long_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")
            raise PromptTooLongError("too long")

        deps = make_fake_deps(prompt_too_long_model, compact=compact)
        events = collect(prompt_too_long_model, deps=deps)
        assert events[-1] == Terminated(reason=Terminal.PROMPT_TOO_LONG)


class TestMessageHelperFunctions:
    def test_is_api_error_via_attribute(self):
        msg = SimpleNamespace(api_error="blocking_limit")
        assert _is_api_error(msg, "blocking_limit") is True  # type: ignore[arg-type]
        assert _is_api_error(msg, "prompt_too_long") is False  # type: ignore[arg-type]

    def test_is_api_error_via_additional_kwargs(self):
        msg = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        assert _is_api_error(msg, "prompt_too_long") is True
        assert _is_api_error(msg, "max_output_tokens") is False

    def test_is_api_error_when_neither_present(self):
        msg = AIMessage(content="")
        assert _is_api_error(msg, "prompt_too_long") is False

    def test_has_hook_stopped_via_attribute(self):
        msg = SimpleNamespace(hook_stopped_continuation=True)
        assert _has_hook_stopped(msg) is True  # type: ignore[arg-type]
        msg_false = SimpleNamespace(hook_stopped_continuation=False)
        assert _has_hook_stopped(msg_false) is False  # type: ignore[arg-type]

    def test_has_hook_stopped_via_additional_kwargs(self):
        msg = ToolMessage(content="", tool_call_id="c1", additional_kwargs={"hook_stopped_continuation": True})
        assert _has_hook_stopped(msg) is True
        msg_false = ToolMessage(content="", tool_call_id="c1", additional_kwargs={"hook_stopped_continuation": False})
        assert _has_hook_stopped(msg_false) is False

    def test_has_hook_stopped_when_neither_present(self):
        msg = ToolMessage(content="", tool_call_id="c1")
        assert _has_hook_stopped(msg) is False


class TestTurnCountAsymmetry:
    """Part A6: turn_count advances only at next_turn; recovery iterations are free."""

    def test_turn_count_does_not_advance_on_stop_hook_blocking(self):
        stop_hook_result = StopHookResult(
            blocking_errors=(HumanMessage(content="must write failing test first"),)
        )
        stop_hooks = FakeStopHooks([stop_hook_result, StopHookResult()])
        model = FakeModel([[AIMessage(content="I am done")], [AIMessage(content="Finished")]])
        deps = make_fake_deps(model, stop_hooks=stop_hooks)
        collect(model, deps=deps)
        assert [s.turn_count for s in model.seen] == [1, 1]

    def test_turn_count_does_not_advance_on_reactive_compact_retry(self):
        compact = FakeCompact([CompactionResult(compacted=True, messages=(HumanMessage(content="c"),))])
        too_long_msg = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        model = FakeModel([[too_long_msg], [AIMessage(content="done")]])
        deps = make_fake_deps(model, compact=compact)
        collect(model, deps=deps)
        assert [s.turn_count for s in model.seen] == [1, 1]

    def test_turn_count_does_not_advance_on_max_output_tokens_recovery(self):
        truncated_msg = AIMessage(content="half code...", additional_kwargs={"api_error": "max_output_tokens"})
        model = FakeModel([[truncated_msg], [AIMessage(content="...rest of code")]])
        deps = make_fake_deps(model)
        collect(model, deps=deps)
        assert [s.turn_count for s in model.seen] == [1, 1]

    def test_turn_count_advances_only_on_next_turn_across_mixed_sequence(self):
        # Sequence:
        # Iteration 1: prompt_too_long -> reactive_compact_retry (turn_count stays 1)
        # Iteration 2: max_output_tokens -> recovery (turn_count stays 1)
        # Iteration 3: asks for tool -> tools run -> next_turn (turn_count advances to 2)
        # Iteration 4: stop_hook blocks -> stop_hook_blocking (turn_count stays 2)
        # Iteration 5: text answer -> completed (turn_count 2)
        compact = FakeCompact([CompactionResult(compacted=True, messages=(HumanMessage(content="compacted"),))])
        stop_hooks = FakeStopHooks([
            StopHookResult(blocking_errors=(HumanMessage(content="retry"),)),
            StopHookResult(),
        ])
        model = FakeModel([
            [AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})],
            [AIMessage(content="cut", additional_kwargs={"api_error": "max_output_tokens"})],
            [asks_for("RunTests", "c1")],
            [AIMessage(content="done1")],
            [AIMessage(content="done2")],
        ])
        deps = make_fake_deps(model, compact=compact, stop_hooks=stop_hooks)
        collect(model, deps=deps)
        assert [s.turn_count for s in model.seen] == [1, 1, 1, 2, 2]


class TestResetPreserveMatrix:
    """
    Part A7: the reset/preserve matrix across all continue sites.

    One executable test per continue site asserting every field on LoopState:
    - NEXT_TURN
    - STOP_HOOK_BLOCKING
    - REACTIVE_COMPACT_RETRY
    - MAX_OUTPUT_TOKENS_RECOVERY
    """

    def test_next_turn_matrix(self):
        initial = start(
            has_attempted_reactive_compact=True,
            stop_hook_active=True,
            turn_count=1,
            phase_ledger=PhaseLedger(phase="GREEN", red_confirmed=True),
            compaction_tracking=CompactionTracking(compacted=True, turn_id="t0", turn_counter=1),
        )
        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        tools = FakeTools()
        collect(model, tools, state=initial)

        next_state = model.seen[1]
        assert len(next_state.messages) == len(initial.messages) + 2
        assert next_state.tool_context.messages == next_state.messages
        assert next_state.phase_ledger == initial.phase_ledger
        assert next_state.compaction_tracking == initial.compaction_tracking
        assert next_state.has_attempted_reactive_compact is False
        assert next_state.stop_hook_active is True
        assert next_state.turn_count == 2
        assert next_state.transition is not None
        assert next_state.transition.reason is Continue.NEXT_TURN

    def test_stop_hook_blocking_matrix(self):
        initial = start(
            has_attempted_reactive_compact=True,
            stop_hook_active=None,
            turn_count=3,
            phase_ledger=PhaseLedger(phase="RED", red_confirmed=False),
            compaction_tracking=CompactionTracking(compacted=False, turn_id="t1", turn_counter=0),
        )
        block_msg = HumanMessage(content="Test never failed in RED phase")
        stop_hooks = FakeStopHooks([StopHookResult(blocking_errors=(block_msg,)), StopHookResult()])
        model = FakeModel([[AIMessage(content="I claim done")], [AIMessage(content="Ok done")]])
        deps = make_fake_deps(model, stop_hooks=stop_hooks)
        collect(model, deps=deps, state=initial)

        next_state = model.seen[1]
        assert next_state.messages[-1] == block_msg
        assert next_state.phase_ledger == initial.phase_ledger
        assert next_state.compaction_tracking == initial.compaction_tracking
        assert next_state.has_attempted_reactive_compact is True
        assert next_state.stop_hook_active is True
        assert next_state.turn_count == 3
        assert next_state.transition is not None
        assert next_state.transition.reason is Continue.STOP_HOOK_BLOCKING

    def test_reactive_compact_retry_matrix_with_new_tracking(self):
        initial = start(
            has_attempted_reactive_compact=False,
            stop_hook_active=True,
            turn_count=2,
            phase_ledger=PhaseLedger(phase="REFACTOR", red_confirmed=True, green_passed=True),
            compaction_tracking=None,
        )
        new_tracking = CompactionTracking(compacted=True, turn_id="c-turn", turn_counter=1)
        compacted_msg = HumanMessage(content="compacted summary")
        compact = FakeCompact([CompactionResult(compacted=True, messages=(compacted_msg,), tracking=new_tracking)])
        too_long = AIMessage(content="", additional_kwargs={"api_error": "prompt_too_long"})
        model = FakeModel([[too_long], [AIMessage(content="done")]])
        deps = make_fake_deps(model, compact=compact)
        collect(model, deps=deps, state=initial)

        next_state = model.seen[1]
        assert next_state.messages == (compacted_msg,)
        assert next_state.phase_ledger == initial.phase_ledger
        assert next_state.compaction_tracking == new_tracking
        assert next_state.has_attempted_reactive_compact is True
        assert next_state.stop_hook_active is None
        assert next_state.turn_count == 2
        assert next_state.transition is not None
        assert next_state.transition.reason is Continue.REACTIVE_COMPACT_RETRY

    def test_reactive_compact_retry_matrix_preserves_existing_tracking_when_tracking_none(self):
        old_tracking = CompactionTracking(compacted=True, turn_id="old-turn", turn_counter=3)
        initial = start(
            has_attempted_reactive_compact=False,
            stop_hook_active=True,
            turn_count=2,
            phase_ledger=PhaseLedger(phase="GREEN", red_confirmed=True),
            compaction_tracking=old_tracking,
        )
        compacted_msg = HumanMessage(content="compacted summary")
        compact = FakeCompact([CompactionResult(compacted=True, messages=(compacted_msg,), tracking=None)])

        async def exc_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if len(compact.seen) == 0:
                raise PromptTooLongError("too long")
            yield AIMessage(content="done")

        deps = make_fake_deps(exc_model, compact=compact)
        collect(exc_model, deps=deps, state=initial)

        next_state = compact.seen[0]
        assert next_state.compaction_tracking == old_tracking

    def test_max_output_tokens_recovery_matrix(self):
        initial = start(
            has_attempted_reactive_compact=True,
            stop_hook_active=True,
            turn_count=4,
            phase_ledger=PhaseLedger(phase="GREEN", red_confirmed=True),
            compaction_tracking=CompactionTracking(compacted=True, turn_id="t-prev", turn_counter=2),
        )
        truncated = AIMessage(content="def foo():\n    return", additional_kwargs={"api_error": "max_output_tokens"})
        model = FakeModel([[truncated], [AIMessage(content=" 42")]])
        deps = make_fake_deps(model)
        collect(model, deps=deps, state=initial)

        next_state = model.seen[1]
        assert next_state.messages[-1].content == "Please continue from where you left off."
        assert next_state.phase_ledger == initial.phase_ledger
        assert next_state.compaction_tracking == initial.compaction_tracking
        assert next_state.has_attempted_reactive_compact is True
        assert next_state.stop_hook_active is None
        assert next_state.turn_count == 4
        assert next_state.transition is not None
        assert next_state.transition.reason is Continue.MAX_OUTPUT_TOKENS_RECOVERY


class TestDrain:
    def test_it_returns_how_the_run_ended(self):
        deps = make_fake_deps(FakeModel([[AIMessage(content="done")]]))
        terminated = asyncio.run(drain(run_loop(start(), CONFIG, deps)))
        assert terminated.reason is Terminal.COMPLETED

    def test_it_refuses_a_run_that_never_terminated(self):
        async def empty() -> AsyncIterator[LoopEvent]:
            yield AIMessage(content="a message and then nothing")

        with pytest.raises(
            LoopNeverTerminated, match=r"^the loop produced no Terminated event$"
        ):
            asyncio.run(drain(empty()))
