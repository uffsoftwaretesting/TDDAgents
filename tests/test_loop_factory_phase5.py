"""Phase 5: the production compaction dependencies and the TDD re-attach they inject."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

import app.query.deps as factory
from app.query.config import build_run_config
from app.query.state import CompactionTracking, initial_loop_state
from app.services.compact.auto_compact import AutoCompactOutcome
from app.services.compact.compact import CompactionResult as CompactResult
from app.services.compact.compact import create_compact_boundary_message
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.ledger import PhaseLedger, TddPhase, TestRunRecord
from app.tool import tool_context_for

CONFIG = build_run_config("r1")


def _state(**app: Any):
    store = AppStateStore(AppState(**app))
    return initial_loop_state((HumanMessage(content="task"),), tool_context_for(store))


def test_post_compact_attachments_reattach_the_tdd_state_from_the_ledger():
    ledger = PhaseLedger(phase=TddPhase.GREEN, tests_written=True)
    run = TestRunRecord(test_path="tests/test_calc.py", exit_code=1, stdout="E   assert add(1, 2) == 3")
    state = _state(phase_ledger=ledger, last_test_run=run)
    (message,) = factory.post_compact_attachments(state.tool_context)
    attachment = message.additional_kwargs["attachment"]
    assert (attachment["type"], attachment["phase"], attachment["tests_written"]) == ("tdd_state", "GREEN", True)
    assert "E   assert add(1, 2) == 3" in str(message.content)
    assert "tests/test_calc.py" in str(message.content)


def test_production_autocompact_binds_the_run(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake(messages, context, **kwargs):
        seen.update(kwargs, messages=messages, context=context)
        return AutoCompactOutcome(was_compacted=False)

    monkeypatch.setattr(factory, "auto_compact_if_needed", fake)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    tracking = CompactionTracking(compacted=False, turn_id="t", turn_counter=0, consecutive_failures=1)
    state = _state()
    from dataclasses import replace

    state = replace(state, compaction_tracking=tracking)
    outcome = asyncio.run(factory.production_autocompact(state.messages, state, CONFIG))
    assert outcome == AutoCompactOutcome(was_compacted=False)
    assert seen["messages"] == state.messages
    assert seen["context"] is state.tool_context
    assert (seen["model"], seen["tracking"], seen["summarizer"]) == (CONFIG.model, tracking, "summarizer")
    assert seen["post_compact_attachments"] is factory.post_compact_attachments
    assert seen["query_source"] == "sdk"  # a headless run is upstream's `sdk` query source


def test_production_compact_is_a_real_reactive_compaction(monkeypatch):
    result = CompactResult(
        boundary_marker=create_compact_boundary_message("auto", 9),
        summary_messages=[HumanMessage(content="summary")],
        attachments=[HumanMessage(content="tdd")],
        hook_results=[],
    )
    seen: dict[str, Any] = {}

    async def fake(messages, context, **kwargs):
        seen.update(kwargs, messages=messages)
        return result

    monkeypatch.setattr(factory, "compact_conversation", fake)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    monkeypatch.setattr(factory, "production_uuid", lambda: "turn-r")
    state = _state()
    out = asyncio.run(factory.production_compact(state, CONFIG))
    assert out.compacted is True
    assert list(out.messages) == [result.boundary_marker, *result.summary_messages, *result.attachments]
    assert out.tracking == CompactionTracking(compacted=True, turn_id="turn-r", turn_counter=0, consecutive_failures=0)
    assert (seen["trigger"], seen["model"], seen["summarizer"]) == ("auto", CONFIG.model, "summarizer")
    assert seen["post_compact_attachments"] is factory.post_compact_attachments


def test_a_failed_reactive_compaction_reports_no_compaction(monkeypatch):
    async def boom(messages, context, **kwargs):
        raise RuntimeError("Not enough messages to compact.")

    monkeypatch.setattr(factory, "compact_conversation", boom)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    state = _state()
    out = asyncio.run(factory.production_compact(state, CONFIG))
    assert (out.compacted, out.messages) == (False, state.messages)


def test_production_deps_bind_the_compaction_slots():
    deps = factory.get_production_deps()
    assert deps.autocompact is factory.production_autocompact
    assert deps.microcompact is factory.production_microcompact
    assert deps.compact is factory.production_compact


def test_both_compactions_point_the_summary_at_the_runs_transcript(monkeypatch):
    """Phase 6: `compact.ts` passes `getTranscriptPath()` to the summary message."""
    seen: list[Any] = []

    async def fake_auto(messages, context, **kwargs):
        seen.append(kwargs["transcript_path"])
        return AutoCompactOutcome(was_compacted=False)

    async def fake_compact(messages, context, **kwargs):
        seen.append(kwargs["transcript_path"])
        raise RuntimeError("stop")

    monkeypatch.setattr(factory, "auto_compact_if_needed", fake_auto)
    monkeypatch.setattr(factory, "compact_conversation", fake_compact)
    monkeypatch.setattr(factory, "build_compact_summarizer", lambda: "summarizer")
    store = AppStateStore(AppState())
    state = initial_loop_state((HumanMessage(content="task"),), tool_context_for(store, transcript_path="/p/r.jsonl"))
    asyncio.run(factory.production_autocompact(state.messages, state, CONFIG))
    asyncio.run(factory.production_compact(state, CONFIG))
    assert seen == ["/p/r.jsonl", "/p/r.jsonl"]
