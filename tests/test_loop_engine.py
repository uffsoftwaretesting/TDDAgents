"""
The loop skeleton (A3), driven entirely by fakes.

No sandbox, no network, no model. The fakes are also the instrumentation: `FakeModel`
records the `LoopState` it was handed on each iteration, which is how these tests observe
the reset/preserve decisions at the `next_turn` site without the loop having to expose its
state. That is the same seam upstream tests through, and it is why A4 widens the deps record
rather than adding accessors.

`asyncio.run` per test rather than a plugin: the repo has no async test dependency and
needs none for this.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.loop.config import RunConfig, build_run_config
from app.loop.context import AppStateStore, tool_context_for
from app.loop.deps import LoopDeps
from app.loop.engine import LoopEvent, LoopNeverTerminated, drain, run_loop
from app.loop.ledger import PhaseLedger
from app.loop.messages import Message, ToolCall
from app.loop.state import CompactionTracking, LoopState, initial_loop_state
from app.loop.transitions import Continue, Terminal, Terminated

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


def start(**overrides) -> LoopState:
    state = initial_loop_state(
        (HumanMessage(content="spec"),), tool_context_for(AppStateStore())
    )
    return replace(state, **overrides) if overrides else state


def collect(model: FakeModel, tools: FakeTools, state: LoopState) -> list[LoopEvent]:
    deps = LoopDeps(call_model=model, run_tools=tools)

    async def go() -> list[LoopEvent]:
        return [event async for event in run_loop(state, CONFIG, deps)]

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

    def two_turns(self, **overrides) -> FakeModel:
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
    Both dependencies are given the run's config and the state of the iteration calling
    them. Nothing else in the suite would notice if the loop passed neither: a fake that
    ignores its arguments cannot fail when they are wrong.
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
        """
        Not the initial state and not the successor: the tools of turn N run against the
        conversation as it stood when turn N's model call was made.
        """
        model, tools = self.run()
        assert tools.seen == [model.seen[0]]


class TestDrain:
    def test_it_returns_how_the_run_ended(self):
        deps = LoopDeps(call_model=FakeModel([[AIMessage(content="done")]]), run_tools=FakeTools())
        terminated = asyncio.run(drain(run_loop(start(), CONFIG, deps)))
        assert terminated.reason is Terminal.COMPLETED

    def test_it_refuses_a_run_that_never_terminated(self):
        async def empty() -> AsyncIterator[LoopEvent]:
            yield AIMessage(content="a message and then nothing")

        with pytest.raises(
            LoopNeverTerminated, match=r"^the loop produced no Terminated event$"
        ):
            asyncio.run(drain(empty()))
