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
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.config import RunConfig, build_run_config
from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
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
        # Phase 5: the turn's attachments follow its results (first announcement of the ledger).
        attachment = events[2]
        assert isinstance(attachment, HumanMessage)
        assert attachment.additional_kwargs["attachment"]["type"] == "tdd_state"
        assert events[3] is answered
        assert events[4] == Terminated(reason=Terminal.COMPLETED)

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
        # The answer, its result, and the attachments that turn produced (Phase 5).
        assert len(seen[1].messages) == len(seen[0].messages) + 3
        assert seen[1].messages[: len(seen[0].messages)] == seen[0].messages
        assert isinstance(seen[1].messages[-2], ToolMessage)
        assert seen[1].messages[-1].additional_kwargs["attachment"]["type"] == "tdd_state"

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

    def test_compaction_tracking_advances_its_turn_counter(self):
        """`query.ts`: `if (tracking?.compacted) tracking.turnCounter++` before `next_turn`."""
        tracking = CompactionTracking(compacted=True, turn_id="t1", turn_counter=2)
        advanced = CompactionTracking(compacted=True, turn_id="t1", turn_counter=3)
        assert self.two_turns(compaction_tracking=tracking).seen[1].compaction_tracking == advanced
        idle = CompactionTracking(compacted=False, turn_id="t1", turn_counter=2)
        assert self.two_turns(compaction_tracking=idle).seen[1].compaction_tracking == idle

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
        # Upstream hands the hooks `[...messagesForQuery, ...assistantMessages]`.
        seen = stop_hooks.seen[0]
        assert seen.messages == (*model.seen[0].messages, AIMessage(content="done"))
        assert replace(seen, messages=model.seen[0].messages) == model.seen[0]

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

    def test_terminal_hook_stopped_on_a_stopped_continuation_attachment(self):
        """`query.ts`: a tool-phase attachment of type `hook_stopped_continuation` ends the run."""
        async def hook_stopping_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig
        ) -> AsyncIterator[Message]:
            yield ToolMessage(content="ran", tool_call_id=calls[0]["id"])
            yield HumanMessage(
                content="stopped",
                additional_kwargs={"is_meta": True, "attachment": {"type": "hook_stopped_continuation"}},
            )

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=hook_stopping_tools)
        assert events[-1] == Terminated(reason=Terminal.HOOK_STOPPED)

    def test_another_attachment_does_not_stop_the_run(self):
        async def annotating_tools(
            calls: tuple[ToolCall, ...], msgs: tuple[Message, ...], s: LoopState, c: RunConfig
        ) -> AsyncIterator[Message]:
            yield ToolMessage(content="ran", tool_call_id=calls[0]["id"])
            yield HumanMessage(
                content="note",
                additional_kwargs={"is_meta": True, "attachment": {"type": "hook_additional_context"}},
            )

        model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
        events = collect(model, tools=annotating_tools)
        assert events[-1] == Terminated(reason=Terminal.COMPLETED)

    def test_terminal_model_error_on_exception(self):
        async def failing_model(s: LoopState, c: RunConfig) -> AsyncIterator[Message]:
            if False:
                yield AIMessage(content="")
            raise RuntimeError("API failure")

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

    def test_has_hook_stopped_reads_the_attachment_type(self):
        stopped = HumanMessage(content="", additional_kwargs={"attachment": {"type": "hook_stopped_continuation"}})
        assert _has_hook_stopped(stopped) is True
        other = HumanMessage(content="", additional_kwargs={"attachment": {"type": "hook_success"}})
        assert _has_hook_stopped(other) is False
        # The pre-Phase-4 flag is no longer a stop signal: only the attachment is.
        legacy = ToolMessage(content="", tool_call_id="c1", additional_kwargs={"hook_stopped_continuation": True})
        assert _has_hook_stopped(legacy) is False

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
        assert len(next_state.messages) == len(initial.messages) + 3
        assert next_state.tool_context.messages == next_state.messages
        assert next_state.phase_ledger == initial.phase_ledger
        assert next_state.compaction_tracking == CompactionTracking(compacted=True, turn_id="t0", turn_counter=2)
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


# ── Phase 1: fallback, API-error guard, history repair, pool refresh ─────────

class FailingModel:
    """Raises on each call until its script of errors runs out, then answers."""

    def __init__(self, errors: list[BaseException], final: list[Message] | None = None) -> None:
        self.errors = list(errors)
        self.final = final if final is not None else [AIMessage(content="done")]
        self.seen_configs: list[RunConfig] = []
        self.emitted: list[list[Message]] = []

    async def __call__(self, state: LoopState, config: RunConfig) -> AsyncIterator[Message]:
        self.seen_configs.append(config)
        emitted: list[Message] = []
        self.emitted.append(emitted)
        if self.errors:
            error = self.errors.pop(0)
            # Emit a tool call first, so the repair invariant has something to repair.
            orphan = asks_for("WriteFile", "orphan-1")
            emitted.append(orphan)
            yield orphan
            raise error
        for final_message in self.final:
            emitted.append(final_message)
            yield final_message


def test_a_model_error_repairs_every_orphaned_tool_call_before_terminating():
    """
    Invariant B7 at the model boundary: a tool_use was emitted, its tool_result never will
    be, and the next request would be malformed without a synthetic one.
    """
    model = FailingModel([RuntimeError("provider exploded")], final=[])
    events = collect(model)

    repairs = [e for e in events if isinstance(e, ToolMessage)]
    assert [r.tool_call_id for r in repairs] == ["orphan-1"]
    assert "provider exploded" in str(repairs[0].content)
    assert repairs[0].additional_kwargs["is_error"] is True
    assert isinstance(events[-1], Terminated) and events[-1].reason == Terminal.MODEL_ERROR


def test_a_model_fallback_repairs_switches_model_and_retries_the_same_turn():
    from app.loop.retry import FallbackTriggeredError

    model = FailingModel([FallbackTriggeredError("big", "small")])
    events = collect(model)

    repairs = [e for e in events if isinstance(e, ToolMessage)]
    assert [r.tool_call_id for r in repairs] == ["orphan-1"]
    assert "Model fallback triggered" in str(repairs[0].content)

    notices = [e for e in events if isinstance(e, SystemMessage)]
    assert "Switched to small due to high demand for big" in str(notices[0].content)

    # The retry is part of the same turn: the model was called twice, on one iteration.
    assert [c.model for c in model.seen_configs] == [CONFIG.model, "small"]
    assert isinstance(events[-1], Terminated) and events[-1].reason == Terminal.COMPLETED


def test_the_abandoned_turn_is_dropped_so_the_fallback_answer_stands_alone():
    """Upstream clears assistantMessages/toolResults/toolUseBlocks before retrying."""
    from app.loop.retry import FallbackTriggeredError

    model = FailingModel([FallbackTriggeredError("big", "small")])
    deps = make_fake_deps(model)
    terminal = asyncio.run(drain(run_loop(start(), CONFIG, deps)))

    assert terminal.reason == Terminal.COMPLETED
    # The orphaned call did not survive into the follow-up: the loop ended without tools.
    assert model.seen_configs[-1].model == "small"


def test_an_api_error_message_skips_the_stop_hooks_entirely():
    """
    `query.ts`'s anti-spiral guard. The TDD stop hook would otherwise block on an
    incomplete cycle, the retry would hit the same provider error, and round it goes.
    """
    error_message = AIMessage(content="overloaded", additional_kwargs={"is_api_error_message": True})
    hooks = FakeStopHooks([StopHookResult(blocking_errors=(HumanMessage(content="finish the cycle"),))])
    model = FakeModel([[error_message]])

    terminal = asyncio.run(drain(run_loop(start(), CONFIG, make_fake_deps(model, stop_hooks=hooks))))

    assert hooks.seen == []
    assert terminal.reason == Terminal.MODEL_ERROR


def test_an_api_error_in_an_earlier_message_does_not_trigger_the_guard():
    """Upstream reads the LAST message only."""
    hooks = FakeStopHooks()
    model = FakeModel([[
        AIMessage(content="overloaded", additional_kwargs={"is_api_error_message": True}),
        AIMessage(content="recovered"),
    ]])

    terminal = asyncio.run(drain(run_loop(start(), CONFIG, make_fake_deps(model, stop_hooks=hooks))))

    assert len(hooks.seen) == 1
    assert terminal.reason == Terminal.COMPLETED


def test_an_ordinary_answer_still_reaches_the_stop_hooks():
    hooks = FakeStopHooks()
    model = FakeModel([[AIMessage(content="all done")]])
    terminal = asyncio.run(drain(run_loop(start(), CONFIG, make_fake_deps(model, stop_hooks=hooks))))
    assert len(hooks.seen) == 1 and terminal.reason == Terminal.COMPLETED


def test_the_tool_pool_is_rebuilt_between_turns_when_the_roster_changes():
    """
    `refreshTools`: this is what lets a RED to GREEN ledger move change the tools the next
    turn is offered. Without it a phase-filtered pool outlives its phase.
    """
    red_pool = (SimpleNamespace(name="WriteTest"),)
    green_pool = (SimpleNamespace(name="WriteFile"),)
    rosters = [green_pool]

    state = start()
    state.tool_context.tools = red_pool
    state.tool_context.refresh_tools = lambda: rosters.pop(0) if rosters else green_pool

    model = FakeModel([[asks_for("WriteTest", "c1")], [AIMessage(content="done")]])
    asyncio.run(drain(run_loop(state, CONFIG, make_fake_deps(model, tools=FakeTools()))))

    assert [t.name for t in model.seen[0].tool_context.tools] == ["WriteTest"]
    assert [t.name for t in model.seen[1].tool_context.tools] == ["WriteFile"]


def test_an_unchanged_roster_keeps_the_very_same_tuple_for_the_prompt_cache():
    """The identity check is the point: an equal-but-new tuple would break the cache."""
    pool = (SimpleNamespace(name="ReadFile"),)
    state = start()
    state.tool_context.tools = pool
    state.tool_context.refresh_tools = lambda: pool

    model = FakeModel([[asks_for("ReadFile", "c1")], [AIMessage(content="done")]])
    asyncio.run(drain(run_loop(state, CONFIG, make_fake_deps(model, tools=FakeTools()))))

    assert model.seen[1].tool_context.tools is pool


def test_a_refresh_callback_that_raises_leaves_the_roster_alone():
    pool = (SimpleNamespace(name="ReadFile"),)
    state = start()
    state.tool_context.tools = pool

    def angry() -> tuple[Any, ...]:
        raise RuntimeError("roster unavailable")

    state.tool_context.refresh_tools = angry
    model = FakeModel([[asks_for("ReadFile", "c1")], [AIMessage(content="done")]])
    asyncio.run(drain(run_loop(state, CONFIG, make_fake_deps(model, tools=FakeTools()))))
    assert model.seen[1].tool_context.tools is pool


def test_a_refresh_callback_returning_none_leaves_the_roster_alone():
    pool = (SimpleNamespace(name="ReadFile"),)
    state = start()
    state.tool_context.tools = pool

    def empty_refresh() -> Any:
        return None

    state.tool_context.refresh_tools = empty_refresh
    model = FakeModel([[asks_for("ReadFile", "c1")], [AIMessage(content="done")]])
    asyncio.run(drain(run_loop(state, CONFIG, make_fake_deps(model, tools=FakeTools()))))
    assert model.seen[1].tool_context.tools is pool


def test_refresh_is_not_consulted_on_a_recovery_continue():
    """Upstream refreshes once per iteration, after tools ran — never on a recovery path."""
    calls: list[int] = []
    state = replace(start(), has_attempted_reactive_compact=False)

    def counting_refresh() -> tuple[Any, ...]:
        calls.append(1)
        return ()

    state.tool_context.refresh_tools = counting_refresh

    model = FakeModel([
        [AIMessage(content="too long", additional_kwargs={"api_error": "prompt_too_long"})],
        [AIMessage(content="done")],
    ])
    compact = FakeCompact([CompactionResult(compacted=True, messages=(HumanMessage(content="small"),))])
    asyncio.run(drain(run_loop(state, CONFIG, make_fake_deps(model, compact=compact))))

    assert calls == []


# ── the two Phase 1 helpers, exercised directly ─────────────────────────────

class TestIsApiErrorMessage:
    def test_a_message_flagged_on_the_object_itself(self):
        from app.loop.engine import is_api_error_message

        message = AIMessage(content="x")
        object.__setattr__(message, "is_api_error_message", True)
        assert is_api_error_message(message) is True

    def test_a_message_flagged_in_additional_kwargs(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(
            AIMessage(content="x", additional_kwargs={"is_api_error_message": True})) is True

    def test_an_api_error_code_also_counts(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(
            AIMessage(content="x", additional_kwargs={"api_error": "prompt_too_long"})) is True

    def test_an_empty_api_error_does_not_count(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(AIMessage(content="x", additional_kwargs={"api_error": ""})) is False

    def test_a_flag_set_to_false_does_not_count(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(
            AIMessage(content="x", additional_kwargs={"is_api_error_message": False})) is False

    def test_an_ordinary_message_is_not_an_api_error(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(AIMessage(content="all good")) is False

    def test_an_object_with_no_additional_kwargs_at_all(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(SimpleNamespace()) is False  # type: ignore[arg-type]

    def test_additional_kwargs_that_is_not_a_mapping(self):
        from app.loop.engine import is_api_error_message

        assert is_api_error_message(SimpleNamespace(additional_kwargs="junk")) is False  # type: ignore[arg-type]


class TestRefreshToolPool:
    def _context(self, tools, refresh=None):
        state = start()
        state.tool_context.tools = tools
        state.tool_context.refresh_tools = refresh
        return state.tool_context

    def test_without_a_callback_the_context_is_returned_unchanged(self):
        from app.loop.engine import refresh_tool_pool

        context = self._context((SimpleNamespace(name="A"),))
        assert refresh_tool_pool(context) is context

    def test_an_identical_tuple_is_returned_unchanged(self):
        from app.loop.engine import refresh_tool_pool

        pool = (SimpleNamespace(name="A"),)
        context = self._context(pool, lambda: pool)
        assert refresh_tool_pool(context) is context

    def test_an_equal_but_new_tuple_still_replaces_the_context(self):
        """Identity, not equality: upstream compares references and so do we."""
        from app.loop.engine import refresh_tool_pool

        tool = SimpleNamespace(name="A")
        context = self._context((tool,), lambda: (tool,))
        refreshed = refresh_tool_pool(context)
        assert refreshed is not context
        assert [t.name for t in refreshed.tools] == ["A"]

    def test_a_changed_roster_is_adopted(self):
        from app.loop.engine import refresh_tool_pool

        context = self._context((SimpleNamespace(name="A"),), lambda: (SimpleNamespace(name="B"),))
        assert [t.name for t in refresh_tool_pool(context).tools] == ["B"]

    def test_a_list_is_accepted_and_stored_as_a_tuple(self):
        from app.loop.engine import refresh_tool_pool

        context = self._context((), lambda: [SimpleNamespace(name="B")])
        assert isinstance(refresh_tool_pool(context).tools, tuple)
