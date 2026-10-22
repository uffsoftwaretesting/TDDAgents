"""Phase 6: the run's cost tracker (`cost-tracker.ts` -> `addToTotalSessionCost`), fed by the
model call's callback the way upstream's is fed by each API response."""

from __future__ import annotations

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.loop.cost import CostTracker, ModelUsage, Usage, usage_from_langchain
from app.loop.cost.tracker import format_total_cost


def _result(usage: dict | None, model: str | None = None, key: str = "model_name") -> LLMResult:
    metadata = {key: model} if model else {}
    message = AIMessage(content="x", usage_metadata=usage, response_metadata=metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _lc_usage(inp: int, out: int, read: int = 0, create: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "input_token_details": {"cache_read": read, "cache_creation": create},
    }


def test_langchain_input_tokens_include_the_cache_and_are_split_out():
    usage = usage_from_langchain(_lc_usage(1000, 50, read=600, create=100))
    assert usage == Usage(input_tokens=300, output_tokens=50, cache_read_input_tokens=600,
                          cache_creation_input_tokens=100)


def test_missing_details_mean_no_cache():
    assert usage_from_langchain({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}) == Usage(7, 3)
    assert usage_from_langchain(None) is None
    assert usage_from_langchain({"input_tokens": 5, "output_tokens": 1, "input_token_details": None}) == Usage(5, 1)


def test_a_response_adds_its_cost_and_tokens():
    tracker = CostTracker(model="fallback")
    tracker.on_llm_end(_result(_lc_usage(1_000_000, 1_000_000), "claude-sonnet-4-5"), run_id=uuid4())
    assert tracker.total_cost_usd == pytest.approx(18)
    assert tracker.has_unknown_model_cost is False
    assert tracker.total_usage() == {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    (name, entry), = tracker.model_usage().items()
    assert name == "claude-sonnet-4-5"
    assert (entry.input_tokens, entry.output_tokens) == (1_000_000, 1_000_000)
    assert entry.cost_usd == pytest.approx(18)


def test_usage_accumulates_per_model_and_in_total():
    tracker = CostTracker(model="fallback")
    tracker.on_llm_end(_result(_lc_usage(100, 10, read=40, create=20), "o4-mini"), run_id=uuid4())
    tracker.on_llm_end(_result(_lc_usage(200, 20), "o4-mini", key="model"), run_id=uuid4())
    tracker.on_llm_end(_result(_lc_usage(10, 1), "claude-haiku-4-5"), run_id=uuid4())
    o4 = tracker.model_usage()["o4-mini"]
    assert (o4.input_tokens, o4.output_tokens, o4.cache_read_input_tokens, o4.cache_creation_input_tokens) == (
        240, 30, 40, 20)
    expected_o4 = (240 * 1.1 + 30 * 4.4 + 40 * 0.275 + 20 * 1.1) / 1_000_000
    assert o4.cost_usd == pytest.approx(expected_o4)
    assert tracker.total_cost_usd == pytest.approx(expected_o4 + (10 * 1 + 1 * 5) / 1_000_000)
    assert tracker.total_usage() == {
        "input_tokens": 250,
        "output_tokens": 31,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 20,
    }


def test_an_unknown_model_is_counted_free_and_flagged():
    tracker = CostTracker(model="fallback")
    tracker.on_llm_end(_result(_lc_usage(500, 5), "mystery-1"), run_id=uuid4())
    assert tracker.total_cost_usd == 0
    assert tracker.has_unknown_model_cost is True
    assert tracker.model_usage()["mystery-1"] == ModelUsage(input_tokens=500, output_tokens=5)
    assert tracker.total_usage()["input_tokens"] == 500


def test_without_a_model_name_the_configured_model_is_charged():
    tracker = CostTracker(model="claude-haiku-4-5")
    tracker.on_llm_end(_result(_lc_usage(1_000_000, 0)), run_id=uuid4())
    assert tracker.total_cost_usd == pytest.approx(1)
    assert list(tracker.model_usage()) == ["claude-haiku-4-5"]


def test_a_response_without_usage_adds_nothing():
    tracker = CostTracker(model="claude-haiku-4-5")
    tracker.on_llm_end(_result(None, "claude-haiku-4-5"), run_id=uuid4())
    tracker.on_llm_end(LLMResult(generations=[]), run_id=uuid4())
    tracker.on_llm_end(LLMResult(generations=[[]]), run_id=uuid4())
    assert tracker.model_usage() == {}
    assert tracker.total_cost_usd == 0


def test_api_duration_is_measured_per_call(monkeypatch):
    import time

    clock = iter([10.0, 11.5, 20.0, 20.25])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    tracker = CostTracker(model="m")
    a, b = uuid4(), uuid4()
    tracker.on_chat_model_start({}, [[]], run_id=a)
    tracker.on_llm_end(_result(None), run_id=a)
    tracker.on_llm_start({}, ["p"], run_id=b)
    tracker.on_llm_end(_result(None), run_id=b)
    assert tracker.total_api_duration_ms == pytest.approx(1750)
    # An end without a start adds no duration.
    tracker.on_llm_end(_result(None), run_id=uuid4())
    assert tracker.total_api_duration_ms == pytest.approx(1750)


def test_a_failed_call_still_counts_its_duration(monkeypatch):
    import time

    clock = iter([1.0, 3.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    tracker = CostTracker(model="m")
    run = uuid4()
    tracker.on_chat_model_start({}, [[]], run_id=run)
    tracker.on_llm_error(RuntimeError("boom"), run_id=run)
    assert tracker.total_api_duration_ms == pytest.approx(2000)
    tracker.on_llm_error(RuntimeError("again"), run_id=run)
    assert tracker.total_api_duration_ms == pytest.approx(2000)


def test_add_to_total_session_cost_returns_the_cost_added():
    tracker = CostTracker(model="m")
    cost = tracker.add_to_total_session_cost(Usage(input_tokens=1_000_000, output_tokens=0), "claude-haiku-4-5")
    assert cost == pytest.approx(1)
    assert tracker.total_cost_usd == pytest.approx(1)


def test_format_total_cost():
    tracker = CostTracker(model="m")
    tracker.add_to_total_session_cost(Usage(input_tokens=1_000_000, output_tokens=0), "claude-haiku-4-5")
    assert format_total_cost(tracker) == "Total cost: $1.00"
    tracker.add_to_total_session_cost(Usage(input_tokens=1, output_tokens=0), "mystery")
    assert format_total_cost(tracker) == (
        "Total cost: $1.00 (costs may be inaccurate due to usage of unknown models)")
    cheap = CostTracker(model="m")
    cheap.add_to_total_session_cost(Usage(input_tokens=1000, output_tokens=0), "claude-haiku-4-5")
    assert format_total_cost(cheap) == "Total cost: $0.0010"
    big = CostTracker(model="m")
    big.add_to_total_session_cost(Usage(input_tokens=1_000_000, output_tokens=0), "claude-opus-4")
    assert format_total_cost(big) == "Total cost: $15.00"


def test_the_context_carries_the_runs_tracker_and_a_worker_inherits_it():
    from types import SimpleNamespace

    from app.loop.agents.definition import AgentDefinition
    from app.loop.agents.subagent import create_subagent
    from app.loop.context import AppStateStore, ToolContext, tool_context_for

    assert ToolContext.__dataclass_fields__["cost_tracker"].default is None
    tracker = CostTracker(model="m")
    parent = tool_context_for(AppStateStore(), workspace=SimpleNamespace(root="/w"), run_id="r", cost_tracker=tracker)
    assert parent.cost_tracker is tracker
    worker = create_subagent(AgentDefinition(name="tester", description="d", prompt="p"), parent, directive="go")
    assert worker.context.cost_tracker is tracker


def test_a_missing_input_count_is_zero():
    assert usage_from_langchain({"output_tokens": 5}) == Usage(0, 5)


def test_a_non_chat_generation_adds_nothing():
    from langchain_core.outputs import Generation

    tracker = CostTracker(model="claude-haiku-4-5")
    tracker.on_llm_end(LLMResult(generations=[[Generation(text="x")]]), run_id=uuid4())
    assert tracker.model_usage() == {}


def test_web_search_requests_accumulate():
    tracker = CostTracker(model="m")
    for _ in range(2):
        tracker.add_to_total_session_cost(Usage(input_tokens=0, output_tokens=0, web_search_requests=2), "m")
    assert tracker.model_usage()["m"].web_search_requests == 4


def test_half_a_dollar_keeps_four_decimals():
    from app.loop.cost.tracker import format_cost

    assert format_cost(0.5) == "$0.5000"
    assert format_cost(0.51) == "$0.51"
