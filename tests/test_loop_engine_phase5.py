"""Phase 5: the loop's pre-model pipeline (`query.ts`) — compact boundary, autocompact,
blocking-limit precheck — and the attachments persisted after a turn's tools."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.loop.compact.auto_compact import AutoCompactOutcome
from app.loop.compact.compact import CompactionResult, create_compact_boundary_message
from app.loop.context.app_state import AppState, AppStateStore
from app.loop.context.tool_context import tool_context_for
from app.loop.engine import drain, run_loop
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.state import CompactionTracking, LoopState, initial_loop_state
from app.loop.transitions import Terminal, Terminated
from tests.test_loop_engine import (
    CONFIG,
    FakeModel,
    FakeTools,
    asks_for,
    make_fake_deps,
)


def run(state: LoopState, deps: Any) -> list[Any]:
    async def go() -> list[Any]:
        return [e async for e in run_loop(state, CONFIG, deps)]

    return asyncio.run(go())


class FakeAutocompact:
    def __init__(self, *outcomes: AutoCompactOutcome) -> None:
        self.outcomes = list(outcomes)
        self.seen: list[tuple[tuple[Any, ...], LoopState]] = []

    async def __call__(self, messages, state, config):
        self.seen.append((tuple(messages), state))
        return self.outcomes.pop(0) if self.outcomes else AutoCompactOutcome(was_compacted=False)


def compaction(*summary: str) -> CompactionResult:
    return CompactionResult(
        boundary_marker=create_compact_boundary_message("auto", 1000),
        summary_messages=[HumanMessage(content=s) for s in summary],
        attachments=[HumanMessage(content="reattached-tdd", additional_kwargs={"is_meta": True})],
        hook_results=[],
    )


def test_the_model_only_sees_history_after_the_last_boundary():
    boundary = create_compact_boundary_message("auto", 10)
    old = HumanMessage(content="old")
    new = HumanMessage(content="new")
    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=FakeAutocompact())
    state = initial_loop_state((old, boundary, new), tool_context_for(AppStateStore()))
    run(state, deps)
    assert list(model.seen[0].messages) == [boundary, new]


def test_an_autocompaction_replaces_the_history_and_starts_fresh_tracking():
    result = compaction("the summary")
    auto = FakeAutocompact(AutoCompactOutcome(was_compacted=True, compaction_result=result, consecutive_failures=0))
    model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model, FakeTools(), uuid=lambda: "turn-x"), autocompact=auto)
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    events = run(state, deps)
    post = [result.boundary_marker, *result.summary_messages, *result.attachments]
    assert list(model.seen[0].messages) == post
    # Upstream yields the post-compact messages to the caller (the transcript) first.
    assert events[: len(post)] == post
    assert model.seen[0].compaction_tracking == CompactionTracking(
        compacted=True, turn_id="turn-x", turn_counter=0, consecutive_failures=0)
    # One completed turn later the counter has advanced (`tracking.turnCounter++`).
    assert model.seen[1].compaction_tracking == CompactionTracking(
        compacted=True, turn_id="turn-x", turn_counter=1, consecutive_failures=0)
    assert list(model.seen[1].messages[: len(post)]) == post


def test_a_failed_autocompaction_records_the_failure_count():
    auto = FakeAutocompact(AutoCompactOutcome(was_compacted=False, consecutive_failures=2))
    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=auto)
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    run(state, deps)
    assert model.seen[0].compaction_tracking == CompactionTracking(
        compacted=False, turn_id="", turn_counter=0, consecutive_failures=2)


def test_the_blocking_limit_stops_before_the_model_is_called(monkeypatch):
    import app.loop.engine as engine

    monkeypatch.setattr(engine, "token_count_with_estimation", lambda messages: 10_000_000)
    # Upstream skips the precheck when reactive compaction will recover (reactive enabled and
    # autocompact enabled); with autocompact off the precheck is the only guard.
    monkeypatch.setattr(engine, "is_auto_compact_enabled", lambda: False)
    model = FakeModel([])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=FakeAutocompact())
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    events = run(state, deps)
    assert model.seen == []
    assert events[-1] == Terminated(reason=Terminal.BLOCKING_LIMIT)
    error = events[-2]
    assert isinstance(error, AIMessage)
    assert error.additional_kwargs["is_api_error_message"] is True


def test_after_tools_the_changed_tdd_state_is_persisted_as_an_attachment():
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))

    async def tools(calls, assistant, state, config) -> AsyncIterator[Any]:
        store.update(lambda s: dataclasses.replace(s, phase_ledger=PhaseLedger(phase=TddPhase.GREEN,
                                                                               red_confirmed=True)))
        yield ToolMessage(content="1 failed", tool_call_id="c1")

    model = FakeModel([[asks_for("RunTests", "c1")], [AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model, tools), autocompact=FakeAutocompact())
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(store))
    run(state, deps)
    second = list(model.seen[1].messages)
    kinds = [((m.additional_kwargs or {}).get("attachment") or {}).get("type") for m in second]
    assert "tdd_state" in kinds
    tdd = second[kinds.index("tdd_state")]
    assert tdd.additional_kwargs["attachment"]["phase"] == "GREEN"
    # The attachment comes after the tool results, in the stored history.
    assert kinds.index("tdd_state") > [i for i, m in enumerate(second) if isinstance(m, ToolMessage)][-1]


def test_the_boundary_is_not_sent_to_the_model(tmp_path, monkeypatch):
    from app.loop.model import build_request_messages

    monkeypatch.setenv("HOME", str(tmp_path))
    boundary = create_compact_boundary_message("auto", 10)
    state = initial_loop_state((boundary, HumanMessage(content="after")), tool_context_for(AppStateStore()))
    request = build_request_messages(state, CONFIG)
    assert boundary not in request
    assert not any(isinstance(m, SystemMessage) and m is not request[0] for m in request)
    assert request[-1].content == "after"


def test_drain_still_ends_with_the_terminal():
    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=FakeAutocompact())
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    assert asyncio.run(drain(run_loop(state, CONFIG, deps))) == Terminated(reason=Terminal.COMPLETED)


def test_the_precheck_defers_to_reactive_compaction_when_both_are_on(monkeypatch):
    import app.loop.engine as engine

    monkeypatch.setattr(engine, "token_count_with_estimation", lambda messages: 10_000_000)
    monkeypatch.setattr(engine, "is_auto_compact_enabled", lambda: True)
    model = FakeModel([[AIMessage(content="done")]])
    deps = dataclasses.replace(make_fake_deps(model), autocompact=FakeAutocompact())
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(AppStateStore()))
    run(state, deps)
    assert len(model.seen) == 1


def test_a_lying_summary_cannot_complete_an_unobserved_cycle(tmp_path, monkeypatch):
    """
    Structural invariant 2 through a compaction: the summary claims green, but the TDD state
    after the boundary is re-attached from the ledger (RED, not confirmed), and the stop hook
    still refuses `completed`.
    """
    import dataclasses as dc

    import app.loop.factory as factory
    from app.loop.compact.compact import compact_conversation

    async def lying_summarizer(messages, summary_request, context, *, max_output_tokens):
        return AIMessage(content="<summary>All tests pass; the cycle is complete.</summary>")

    async def compact_once(messages, state, config):
        if any(getattr(m, "additional_kwargs", {}).get("compact_boundary") for m in messages):
            return AutoCompactOutcome(was_compacted=False)
        result = await compact_conversation(
            messages, state.tool_context, summarizer=lying_summarizer, trigger="auto",
            post_compact_attachments=factory.post_compact_attachments, model=config.model,
        )
        return AutoCompactOutcome(was_compacted=True, compaction_result=result, consecutive_failures=0)

    monkeypatch.setenv("HOME", str(tmp_path))
    model = FakeModel([[AIMessage(content="done")], [AIMessage(content="done")]])
    deps = dc.replace(factory.get_production_deps(), call_model=model, autocompact=compact_once)
    store = AppStateStore(AppState(phase_ledger=PhaseLedger()))
    state = initial_loop_state((HumanMessage(content="task"), AIMessage(content="working")), tool_context_for(store))
    events = run(state, deps)

    after_boundary = list(model.seen[0].messages)
    tdd = [m for m in after_boundary if (m.additional_kwargs.get("attachment") or {}).get("type") == "tdd_state"]
    assert tdd and tdd[-1].additional_kwargs["attachment"]["red_confirmed"] is False
    assert events[-1] != Terminated(reason=Terminal.COMPLETED)
    assert events[-1] == Terminated(reason=Terminal.STOP_HOOK_PREVENTED)
