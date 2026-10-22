"""Phase 5 F2: `services/compact/autoCompact.ts` — thresholds, warning state, autocompact."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.loop.compact import auto_compact as ac
from app.loop.compact.auto_compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    ERROR_THRESHOLD_BUFFER_TOKENS,
    MANUAL_COMPACT_BUFFER_TOKENS,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    WARNING_THRESHOLD_BUFFER_TOKENS,
    AutoCompactOutcome,
    auto_compact_if_needed,
    calculate_token_warning_state,
    get_auto_compact_threshold,
    is_auto_compact_enabled,
    should_auto_compact,
)

MODEL = "o4-mini"  # unknown to the window table: 200k window, 32k max output -> 180k effective


def test_upstream_constants():
    assert (AUTOCOMPACT_BUFFER_TOKENS, WARNING_THRESHOLD_BUFFER_TOKENS, ERROR_THRESHOLD_BUFFER_TOKENS) == (
        13_000, 20_000, 20_000)
    assert MANUAL_COMPACT_BUFFER_TOKENS == 3_000
    assert MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES == 3


def test_threshold_and_percent_override(monkeypatch):
    for name in ("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", "TDDAGENTS_AUTO_COMPACT_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    assert get_auto_compact_threshold(MODEL) == 167_000
    monkeypatch.setenv("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", "50")
    assert get_auto_compact_threshold(MODEL) == 90_000
    monkeypatch.setenv("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", "99")
    assert get_auto_compact_threshold(MODEL) == 167_000  # never above the buffered threshold
    for bad in ("0", "101", "abc", "-5"):
        monkeypatch.setenv("TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", bad)
        assert get_auto_compact_threshold(MODEL) == 167_000


def test_is_auto_compact_enabled(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_COMPACT", raising=False)
    monkeypatch.delenv("TDDAGENTS_DISABLE_AUTO_COMPACT", raising=False)
    assert is_auto_compact_enabled() is True
    monkeypatch.setenv("TDDAGENTS_DISABLE_AUTO_COMPACT", "1")
    assert is_auto_compact_enabled() is False
    monkeypatch.delenv("TDDAGENTS_DISABLE_AUTO_COMPACT")
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMPACT", "true")
    assert is_auto_compact_enabled() is False


def test_warning_state_with_autocompact(monkeypatch):
    for name in ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT",
                 "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE", "TDDAGENTS_BLOCKING_LIMIT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    low = calculate_token_warning_state(0, MODEL)
    assert low.percent_left == 100
    assert (low.is_above_warning_threshold, low.is_above_error_threshold) == (False, False)
    assert (low.is_above_auto_compact_threshold, low.is_at_blocking_limit) == (False, False)
    edge = calculate_token_warning_state(147_000, MODEL)  # threshold 167k - 20k
    assert edge.is_above_warning_threshold is True and edge.is_above_error_threshold is True
    assert calculate_token_warning_state(146_999, MODEL).is_above_warning_threshold is False
    assert calculate_token_warning_state(167_000, MODEL).is_above_auto_compact_threshold is True
    assert calculate_token_warning_state(166_999, MODEL).is_above_auto_compact_threshold is False
    assert calculate_token_warning_state(177_000, MODEL).is_at_blocking_limit is True  # 180k - 3k
    assert calculate_token_warning_state(176_999, MODEL).is_at_blocking_limit is False
    assert calculate_token_warning_state(83_500, MODEL).percent_left == 50  # round((167k-83.5k)/167k*100)
    assert calculate_token_warning_state(400_000, MODEL).percent_left == 0


def test_warning_state_without_autocompact_and_blocking_override(monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DISABLE_AUTO_COMPACT", "1")
    monkeypatch.delenv("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE", raising=False)
    state = calculate_token_warning_state(160_000, MODEL)
    assert state.is_above_auto_compact_threshold is False
    # The threshold is the effective window (180k): warning at 160k.
    assert state.is_above_warning_threshold is True
    assert calculate_token_warning_state(159_999, MODEL).is_above_warning_threshold is False
    monkeypatch.setenv("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE", "1000")
    assert calculate_token_warning_state(1000, MODEL).is_at_blocking_limit is True
    assert calculate_token_warning_state(999, MODEL).is_at_blocking_limit is False
    monkeypatch.setenv("TDDAGENTS_BLOCKING_LIMIT_OVERRIDE", "-3")
    assert calculate_token_warning_state(176_999, MODEL).is_at_blocking_limit is False


def _usage_message(tokens: int) -> AIMessage:
    return AIMessage(content="ok", id="a1", usage_metadata={"input_tokens": tokens, "output_tokens": 0,
                                                            "total_tokens": tokens})


def test_should_auto_compact(monkeypatch):
    for name in ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    big = [HumanMessage(content="q"), _usage_message(170_000)]
    small = [HumanMessage(content="q"), _usage_message(1_000)]
    assert should_auto_compact(big, MODEL) is True
    assert should_auto_compact(small, MODEL) is False
    assert should_auto_compact(big, MODEL, query_source="compact") is False
    assert should_auto_compact(big, MODEL, query_source="session_memory") is False
    assert should_auto_compact(big, MODEL, snip_tokens_freed=10_000) is False
    monkeypatch.setenv("TDDAGENTS_DISABLE_AUTO_COMPACT", "1")
    assert should_auto_compact(big, MODEL) is False


class _Recorder:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = raises

    async def __call__(self, messages, context, **kwargs):
        self.calls.append({"messages": messages, "context": context, **kwargs})
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(marker="result")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


#: Never called: these paths return before any summary request.
_NO_SUMMARIZER: Any = None


def _ctx() -> Any:
    from app.loop.context import AppStateStore, tool_context_for

    return tool_context_for(AppStateStore(), run_id="r1")


def test_auto_compact_compacts_and_runs_cleanup(monkeypatch):
    for name in ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    compact = _Recorder()
    cleanups: list[Any] = []
    monkeypatch.setattr(ac, "compact_conversation", compact)
    monkeypatch.setattr(ac, "run_post_compact_cleanup", lambda ctx, **kw: cleanups.append((ctx, kw)))
    ctx = _ctx()
    summarizer: Any = object()
    attach = (lambda c: ())
    messages = [HumanMessage(content="q"), _usage_message(170_000)]
    out = _run(auto_compact_if_needed(messages, ctx, model=MODEL, tracking=None, summarizer=summarizer,
                                      post_compact_attachments=attach, transcript_path="/t"))
    assert (out.was_compacted, out.consecutive_failures) == (True, 0)
    assert out.compaction_result.marker == "result"
    call = compact.calls[0]
    assert call["messages"] == messages and call["context"] is ctx
    assert call["summarizer"] is summarizer and call["post_compact_attachments"] is attach
    assert (call["trigger"], call["model"], call["suppress_follow_up_questions"]) == ("auto", MODEL, True)
    assert call["custom_instructions"] is None and call["transcript_path"] == "/t"
    assert cleanups == [(ctx, {"query_source": None})]


def test_auto_compact_skips_below_threshold_and_when_disabled(monkeypatch):
    for name in ("TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    compact = _Recorder()
    monkeypatch.setattr(ac, "compact_conversation", compact)
    small = [HumanMessage(content="q"), _usage_message(10)]
    assert _run(auto_compact_if_needed(small, _ctx(), model=MODEL, tracking=None, summarizer=_NO_SUMMARIZER,
                                       post_compact_attachments=lambda c: ())) == AutoCompactOutcome(False)
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMPACT", "1")
    big = [HumanMessage(content="q"), _usage_message(170_000)]
    assert _run(auto_compact_if_needed(big, _ctx(), model=MODEL, tracking=None, summarizer=_NO_SUMMARIZER,
                                       post_compact_attachments=lambda c: ())) == AutoCompactOutcome(False)
    assert compact.calls == []


def test_the_circuit_breaker_trips_after_three_failures(monkeypatch):
    for name in ("TDDAGENTS_DISABLE_COMPACT", "TDDAGENTS_DISABLE_AUTO_COMPACT", "TDDAGENTS_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    compact = _Recorder(raises=RuntimeError("summary failed"))
    monkeypatch.setattr(ac, "compact_conversation", compact)
    big = [HumanMessage(content="q"), _usage_message(170_000)]

    def go(failures: int | None) -> AutoCompactOutcome:
        tracking = None if failures is None else SimpleNamespace(consecutive_failures=failures)
        outcome: AutoCompactOutcome = _run(auto_compact_if_needed(
            big, _ctx(), model=MODEL, tracking=tracking, summarizer=_NO_SUMMARIZER,
            post_compact_attachments=lambda c: ()))
        return outcome

    assert go(None) == AutoCompactOutcome(False, None, 1)
    assert go(1) == AutoCompactOutcome(False, None, 2)
    assert go(2) == AutoCompactOutcome(False, None, 3)
    calls = len(compact.calls)
    assert go(3) == AutoCompactOutcome(False)  # tripped: not even attempted
    assert len(compact.calls) == calls
