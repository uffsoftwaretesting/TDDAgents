"""Phase 6: the per-run `QueryEngine` (`QueryEngine.ts` -> `submitMessage`) — transcript
before the loop, whole messages as they arrive, usage and cost, and the result message."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.compact.auto_compact import AutoCompactOutcome
from app.loop.compact.compact import CompactionResult, create_compact_boundary_message
from app.loop.context import AppStateStore, tool_context_for
from app.loop.query_engine import (
    QueryEngine,
    ResultMessage,
    is_result_successful,
    normalize_stop_reason,
)
from app.loop.state import initial_loop_state
from app.loop.transitions import Terminal, Terminated
from tests.test_loop_engine import (
    CONFIG,
    FakeModel,
    FakeTools,
    asks_for,
    make_fake_deps,
)


class FakeTranscript:
    """Records a snapshot of what each `record_transcript` call was handed."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def record_transcript(self, messages):
        self.calls.append(list(messages))


class FakeCost:
    total_cost_usd = 0.25
    total_api_duration_ms = 40

    def total_usage(self):
        return {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1,
                "cache_creation_input_tokens": 0}

    def model_usage(self):
        return {"m": {"costUSD": 0.25}}


def _state(*messages):
    return initial_loop_state(messages or (HumanMessage(content="task"),), tool_context_for(AppStateStore()))


def _run(engine: QueryEngine, state) -> list[Any]:
    async def go():
        return [e async for e in engine.submit(state)]

    return asyncio.run(go())


def _engine(model, tools=None, transcript=None, cost=None, autocompact=None):
    import dataclasses

    deps = make_fake_deps(model, tools)
    if autocompact is not None:
        deps = dataclasses.replace(deps, autocompact=autocompact)
    times = iter([100.0, 100.5, 101.0, 102.0])
    return QueryEngine(CONFIG, deps, transcript=transcript or FakeTranscript(), cost_tracker=cost,
                       session_id="s1", now=lambda: next(times), uuid=lambda: "res-uuid")


def test_the_initial_messages_are_recorded_before_the_model_is_called():
    transcript = FakeTranscript()
    seen_at_call: list[int] = []

    class Model(FakeModel):
        async def __call__(self, state, config):
            seen_at_call.append(len(transcript.calls))
            async for m in super().__call__(state, config):
                yield m

    task = HumanMessage(content="task")
    _run(_engine(Model([[AIMessage(content="done")]]), transcript=transcript), _state(task))
    assert seen_at_call == [1]
    assert transcript.calls[0] == [task]


def test_every_whole_message_is_recorded_on_the_growing_history():
    transcript = FakeTranscript()
    task = HumanMessage(content="task")
    ask = asks_for("RunTests", "c1")
    done = AIMessage(content="done")
    events = _run(_engine(FakeModel([[ask], [done]]), FakeTools(), transcript), _state(task))
    result_tool = next(e for e in events if isinstance(e, ToolMessage))
    histories = transcript.calls
    assert histories[1] == [task, ask]
    assert histories[2] == [task, ask, result_tool]
    assert histories[-1][-1] is done


def test_the_loop_events_pass_through_and_the_result_comes_last():
    done = AIMessage(content="all green", response_metadata={"stop_reason": "end_turn"})
    events = _run(_engine(FakeModel([[done]]), cost=FakeCost()), _state())
    assert events[0] is done
    assert events[-2] == Terminated(reason=Terminal.COMPLETED)
    result = events[-1]
    assert isinstance(result, ResultMessage)
    assert (result.type, result.subtype, result.is_error) == ("result", "success", False)
    assert result.result == "all green"
    assert (result.stop_reason, result.terminal_reason, result.session_id) == ("end_turn", "completed", "s1")
    assert (result.num_turns, result.duration_ms, result.duration_api_ms) == (1, 500, 40)
    assert result.total_cost_usd == 0.25
    assert result.usage["input_tokens"] == 10
    assert result.model_usage == {"m": {"costUSD": 0.25}}
    assert result.uuid == "res-uuid"
    assert result.errors == ()


def test_without_a_cost_tracker_usage_is_empty():
    result = _run(_engine(FakeModel([[AIMessage(content="ok")]])), _state())[-1]
    assert result.total_cost_usd == 0.0
    assert result.usage == {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}
    assert (result.model_usage, result.duration_api_ms) == ({}, 0)


def test_each_user_message_from_the_loop_is_a_turn():
    events = _run(_engine(FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="d")]]), FakeTools()), _state())
    assert events[-1].num_turns == 2


def test_attachments_are_not_turns_and_not_the_result():
    from app.loop.hooks.messages import ATTACHMENT_KEY

    att = HumanMessage(content="r", additional_kwargs={"is_meta": True, ATTACHMENT_KEY: {"type": "tdd_state"}})
    model = FakeModel([[AIMessage(content="text"), att]])
    result = _run(_engine(model), _state())[-1]
    assert (result.num_turns, result.subtype, result.result) == (1, "success", "text")


def test_a_run_ending_on_a_tool_call_is_an_error_during_execution():
    events = _run(_engine(FakeModel([[asks_for("RunTests", "c1")], []]), FakeTools()), _state())
    result = events[-1]
    # The last turn-relevant message is the tool result: a user message of tool results only.
    assert result.subtype == "success"
    events = _run(_engine(FakeModel([[AIMessage(content="", tool_calls=[])]])), _state())
    result = events[-1]
    assert (result.subtype, result.is_error) == ("error_during_execution", True)
    assert result.errors == (
        "[ede_diagnostic] result_type=assistant last_content_type=none stop_reason=None",
    )
    assert result.result is None


def test_an_api_error_message_is_a_success_with_is_error():
    err = AIMessage(content="API Error: boom", additional_kwargs={"is_api_error_message": True})
    result = _run(_engine(FakeModel([[err]])), _state())[-1]
    assert (result.subtype, result.is_error, result.result) == ("success", True, "API Error: boom")


def test_the_last_stop_reason_is_kept():
    first = AIMessage(content="a", response_metadata={"finish_reason": "tool_calls"})
    second = AIMessage(content="b", response_metadata={})
    result = _run(_engine(FakeModel([[first, second]])), _state())[-1]
    assert result.stop_reason == "tool_use"


def test_a_compact_boundary_is_recorded_and_the_history_restarts_from_it():
    transcript = FakeTranscript()
    boundary = create_compact_boundary_message("auto", 9)
    summary = HumanMessage(content="summary", additional_kwargs={"is_compact_summary": True})
    result = CompactionResult(boundary_marker=boundary, summary_messages=[summary], attachments=[], hook_results=[])

    async def auto(messages, state, config):
        return AutoCompactOutcome(was_compacted=True, compaction_result=result, consecutive_failures=0)

    engine = _engine(FakeModel([[AIMessage(content="done")]]), transcript=transcript, autocompact=auto)
    old = HumanMessage(content="old")
    _run(engine, _state(old))
    after_boundary = [c for c in transcript.calls if boundary in c]
    assert after_boundary[0] == [old, boundary]
    assert after_boundary[1] == [boundary, summary]
    assert after_boundary[-1][0] is boundary and old not in after_boundary[-1]


def test_other_system_messages_are_not_recorded():
    transcript = FakeTranscript()
    note = SystemMessage(content="model switched")
    _run(_engine(FakeModel([[note, AIMessage(content="d")]]), transcript=transcript), _state())
    assert all(note not in c for c in transcript.calls)


def test_persist_off_records_nothing():
    transcript = FakeTranscript()
    engine = _engine(FakeModel([[AIMessage(content="d")]]), transcript=transcript)
    engine.persist_session = False
    _run(engine, _state())
    assert transcript.calls == []


class TestIsResultSuccessful:
    def test_assistant_text_or_thinking_last(self):
        assert is_result_successful(AIMessage(content="x")) is True
        assert is_result_successful(AIMessage(content=[{"type": "thinking", "thinking": "t"}])) is True
        assert is_result_successful(AIMessage(content=[{"type": "redacted_thinking", "data": "d"}])) is True
        assert is_result_successful(AIMessage(content=[{"type": "text", "text": "x"}, {"type": "tool_use"}])) is False
        assert is_result_successful(AIMessage(content="", tool_calls=[{"name": "A", "args": {}, "id": "1"}])) is False

    def test_tool_results_are_a_valid_end(self):
        assert is_result_successful(ToolMessage(content="r", tool_call_id="c")) is True

    def test_a_prompt_is_only_success_on_end_turn(self):
        assert is_result_successful(HumanMessage(content="p")) is False
        assert is_result_successful(HumanMessage(content="p"), "end_turn") is True
        assert is_result_successful(None) is False


def test_stop_reasons_are_normalized_to_upstreams_vocabulary():
    assert normalize_stop_reason(AIMessage(content="", response_metadata={"stop_reason": "end_turn"})) == "end_turn"
    assert normalize_stop_reason(AIMessage(content="", response_metadata={"finish_reason": "stop"})) == "end_turn"
    assert normalize_stop_reason(AIMessage(content="", response_metadata={"finish_reason": "length"})) == "max_tokens"
    assert normalize_stop_reason(AIMessage(content="", response_metadata={"finish_reason": "weird"})) == "weird"
    assert normalize_stop_reason(AIMessage(content="", response_metadata={})) is None


def test_a_synthetic_sentinel_is_never_the_result_text():
    from app.loop.storage.recovery import NO_RESPONSE_REQUESTED

    result = _run(_engine(FakeModel([[AIMessage(content=NO_RESPONSE_REQUESTED)]])), _state())[-1]
    assert (result.subtype, result.result) == ("success", "")


def test_the_recorded_run_resumes_as_the_same_conversation(tmp_path):
    """End to end: the engine writes the chain, `load_conversation_for_resume` reads it back."""
    from app.loop.storage.recovery import load_conversation_for_resume
    from app.loop.storage.session_storage import TranscriptStore

    store = TranscriptStore("sess", tmp_path, version="v", git_branch=None)
    task = HumanMessage(content="task")
    ask = asks_for("RunTests", "c1")
    done = AIMessage(content="done")
    engine = QueryEngine(CONFIG, make_fake_deps(FakeModel([[ask], [done]])), transcript=store, session_id="sess")
    events = _run(engine, _state(task))
    loaded = load_conversation_for_resume(store.path)
    assert loaded is not None
    tool_result = next(e for e in events if isinstance(e, ToolMessage))
    assert [type(m) for m in loaded.messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert [m.content for m in loaded.messages] == [task.content, "", tool_result.content, "done"]
    assert isinstance(loaded.messages[1], AIMessage) and loaded.messages[1].tool_calls == ask.tool_calls
    assert loaded.turn_interruption_state.kind == "none"
