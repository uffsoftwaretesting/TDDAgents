"""
The retry layer: `app/services/api/with_retry.py`, the port of `withRetry.ts`.

Every constant asserted here is upstream's, so a drift in either direction is a failure.
The schedule is checked with a deterministic `random` so the jitter is pinned rather than
tolerated.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.api.with_retry import (
    BASE_DELAY_MS,
    CONTEXT_OVERFLOW_SAFETY_BUFFER,
    DEFAULT_MAX_DELAY_MS,
    DEFAULT_MAX_RETRIES,
    FLOOR_OUTPUT_TOKENS,
    JITTER_FACTOR,
    MAX_529_RETRIES,
    CannotRetryError,
    FallbackTriggeredError,
    RetryContext,
    RetryNotice,
    RetryOptions,
    error_status,
    get_retry_after,
    get_retry_delay,
    is_529_error,
    is_connection_error,
    js_parse_int,
    parse_max_tokens_context_overflow,
    should_retry,
    with_retry,
)


class FakeError(Exception):
    def __init__(self, message: str = "boom", status: int | None = None, headers: Any = None) -> None:
        super().__init__(message)
        if status is not None:
            self.status_code = status
        if headers is not None:
            self.headers = headers


class HeaderBag:
    """A `Headers`-like object, the second shape `getRetryAfter` tolerates."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str) -> str | None:
        return self._values.get(name)


# ── constants ────────────────────────────────────────────────────────────────

def test_constants_match_upstream():
    assert (DEFAULT_MAX_RETRIES, BASE_DELAY_MS, DEFAULT_MAX_DELAY_MS) == (10, 500, 32_000)
    assert (JITTER_FACTOR, MAX_529_RETRIES) == (0.25, 3)
    assert (FLOOR_OUTPUT_TOKENS, CONTEXT_OVERFLOW_SAFETY_BUFFER) == (3000, 1000)


# ── js_parse_int ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("5", 5), ("  7 ", 7), ("5s", 5), ("-3", -3), ("+4", 4), ("5.9", 5),
    ("abc", None), ("", None), (None, None), ("s5", None),
])
def test_js_parse_int_matches_parse_int_semantics(raw, expected):
    assert js_parse_int(raw) == expected


# ── get_retry_delay ──────────────────────────────────────────────────────────

def test_backoff_doubles_from_the_base_delay_and_caps_before_jitter():
    schedule = [get_retry_delay(a, rand=lambda: 0.0) for a in range(1, 9)]
    assert schedule == [500, 1000, 2000, 4000, 8000, 16000, 32000, 32000]


def test_jitter_is_additive_one_sided_and_at_most_a_quarter():
    assert get_retry_delay(1, rand=lambda: 0.0) == 500
    assert get_retry_delay(1, rand=lambda: 1.0) == 625
    assert get_retry_delay(1, rand=lambda: 0.5) == 562.5
    # The real ceiling is the cap plus its jitter, not the cap.
    assert get_retry_delay(99, rand=lambda: 1.0) == DEFAULT_MAX_DELAY_MS * 1.25


def test_retry_after_wins_and_bypasses_both_the_cap_and_the_jitter():
    assert get_retry_delay(1, "120", rand=lambda: 1.0) == 120_000
    assert get_retry_delay(8, "1", rand=lambda: 1.0) == 1000


def test_an_http_date_retry_after_is_not_understood_and_falls_back_to_backoff():
    """Upstream parses `retry-after` with parseInt only; a date yields NaN. Faithful port."""
    assert get_retry_delay(1, "Wed, 21 Oct 2026 07:28:00 GMT", rand=lambda: 0.0) == 500


def test_an_empty_retry_after_header_is_ignored():
    assert get_retry_delay(2, "", rand=lambda: 0.0) == 1000


def test_a_custom_max_delay_caps_earlier():
    assert get_retry_delay(10, max_delay_ms=1000, rand=lambda: 0.0) == 1000


# ── header and status reading ────────────────────────────────────────────────

def test_retry_after_is_read_from_both_header_shapes():
    assert get_retry_after(FakeError(headers={"retry-after": "3"})) == "3"
    assert get_retry_after(FakeError(headers={"Retry-After": "4"})) == "4"
    assert get_retry_after(FakeError(headers=HeaderBag({"retry-after": "5"}))) == "5"
    assert get_retry_after(FakeError()) is None


def test_a_header_bag_that_raises_is_not_a_retry_signal():
    class Angry:
        def get(self, name: str) -> str:
            raise RuntimeError("no headers for you")

    assert get_retry_after(FakeError(headers=Angry())) is None


def test_status_is_read_off_the_error_or_its_response():
    assert error_status(FakeError(status=429)) == 429

    class WithResponse(Exception):
        def __init__(self) -> None:
            super().__init__("x")
            self.response = type("R", (), {"status_code": 503})()

    assert error_status(WithResponse()) == 503
    assert error_status(FakeError()) is None


def test_a_boolean_status_is_not_a_status():
    err = FakeError()
    err.status_code = True
    assert error_status(err) is None


# ── should_retry ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (408, True), (409, True), (429, True), (401, True),
    (500, True), (502, True), (529, True),
    (400, False), (404, False), (403, False), (422, False),
])
def test_status_table_matches_upstream(status, expected):
    assert should_retry(FakeError(status=status)) is expected


def test_a_revoked_oauth_403_retries_but_an_ordinary_403_does_not():
    assert should_retry(FakeError("OAuth token has been revoked", status=403)) is True
    assert should_retry(FakeError("forbidden", status=403)) is False


def test_an_overloaded_marker_retries_even_without_a_status():
    assert should_retry(FakeError('{"type":"overloaded_error"}')) is True
    assert is_529_error(FakeError('{"type":"overloaded_error"}')) is True
    assert is_529_error(FakeError(status=529)) is True
    assert is_529_error(FakeError(status=500)) is False


def test_x_should_retry_overrides_the_status_table_in_both_directions():
    assert should_retry(FakeError(status=404, headers={"x-should-retry": "true"})) is True
    assert should_retry(FakeError(status=500, headers={"x-should-retry": "false"})) is False


def test_connection_failures_retry_without_a_status():
    assert should_retry(ConnectionError("reset")) is True
    assert should_retry(TimeoutError("slow")) is True
    assert is_connection_error(type("APIConnectionError", (Exception,), {})()) is True
    assert is_connection_error(FakeError()) is False


def test_an_error_with_no_status_and_no_signal_is_final():
    assert should_retry(ValueError("nothing to go on")) is False


# ── context overflow ─────────────────────────────────────────────────────────

def test_context_overflow_is_parsed_only_from_a_400():
    msg = "input length and `max_tokens` exceed context limit: 180000 + 40000 > 200000"
    assert parse_max_tokens_context_overflow(FakeError(msg, status=400)) == (180000, 40000, 200000)
    assert parse_max_tokens_context_overflow(FakeError(msg, status=500)) is None
    assert parse_max_tokens_context_overflow(FakeError("unrelated", status=400)) is None


# ── with_retry ───────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


async def _noop_sleep(_ms: float) -> None:
    return None


def _options(**kwargs: Any) -> RetryOptions:
    base: dict[str, Any] = {"model": "m", "sleep": _noop_sleep, "random": lambda: 0.0}
    base.update(kwargs)
    return RetryOptions(**base)


def test_a_successful_first_attempt_never_sleeps():
    slept: list[float] = []

    async def sleep(ms: float) -> None:
        slept.append(ms)

    async def op(attempt: int, ctx: RetryContext) -> str:
        return f"ok-{attempt}-{ctx.model}"

    assert _run(with_retry(op, _options(sleep=sleep))) == "ok-1-m"
    assert slept == []


def test_it_retries_until_success_and_follows_the_backoff_schedule():
    slept: list[float] = []
    attempts: list[int] = []

    async def sleep(ms: float) -> None:
        slept.append(ms)

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        if attempt < 4:
            raise FakeError(status=500)
        return "done"

    assert _run(with_retry(op, _options(sleep=sleep))) == "done"
    assert attempts == [1, 2, 3, 4]
    assert slept == [500, 1000, 2000]


def test_the_ceiling_is_max_retries_plus_one_attempts():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(status=500)

    with pytest.raises(CannotRetryError) as excinfo:
        _run(with_retry(op, _options(max_retries=3)))
    assert attempts == [1, 2, 3, 4]
    original = excinfo.value.original_error
    assert original is not None and error_status(original) == 500


def test_max_retries_zero_means_exactly_one_attempt():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(status=500)

    with pytest.raises(CannotRetryError):
        _run(with_retry(op, _options(max_retries=0)))
    assert attempts == [1]


def test_an_unretryable_error_stops_on_the_first_attempt():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(status=404)

    with pytest.raises(CannotRetryError) as excinfo:
        _run(with_retry(op, _options()))
    assert attempts == [1]
    assert excinfo.value.context is not None and excinfo.value.context.model == "m"


def test_the_exhausted_refusal_also_carries_its_context():
    async def op(attempt: int, ctx: RetryContext) -> str:
        raise FakeError(status=500)

    with pytest.raises(CannotRetryError) as excinfo:
        _run(with_retry(op, _options(max_retries=1)))
    assert excinfo.value.context is not None and excinfo.value.context.model == "m"


def test_notices_carry_the_attempt_the_delay_and_the_error():
    notices: list[RetryNotice] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        if attempt == 1:
            raise FakeError(status=503)
        return "ok"

    _run(with_retry(op, _options(on_notice=notices.append)))
    assert len(notices) == 1
    assert (notices[0].attempt, notices[0].delay_ms, notices[0].max_retries) == (1, 500, 10)
    assert error_status(notices[0].error) == 503  # noqa: E501


def test_retry_after_is_honoured_as_the_sleep():
    slept: list[float] = []

    async def sleep(ms: float) -> None:
        slept.append(ms)

    async def op(attempt: int, ctx: RetryContext) -> str:
        if attempt == 1:
            raise FakeError(status=429, headers={"retry-after": "7"})
        return "ok"

    _run(with_retry(op, _options(sleep=sleep)))
    assert slept == [7000]


def test_three_consecutive_overloads_trigger_the_fallback():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(status=529)

    with pytest.raises(FallbackTriggeredError) as excinfo:
        _run(with_retry(op, _options(fallback_model="small")))
    assert len(attempts) == MAX_529_RETRIES
    assert (excinfo.value.original_model, excinfo.value.fallback_model) == ("m", "small")
    assert "m -> small" in str(excinfo.value)


def test_without_a_fallback_model_overloads_just_retry():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        if attempt < 5:
            raise FakeError(status=529)
        return "ok"

    assert _run(with_retry(op, _options())) == "ok"
    assert attempts == [1, 2, 3, 4, 5]


def test_a_pre_seeded_overload_count_carries_across_the_boundary():
    """`initialConsecutive529Errors`: a 529 seen while streaming still counts."""
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(status=529)

    with pytest.raises(FallbackTriggeredError):
        _run(with_retry(op, _options(fallback_model="small", initial_consecutive_529_errors=2)))
    assert len(attempts) == 1


def test_a_context_overflow_shrinks_the_output_budget_and_retries_without_sleeping():
    slept: list[float] = []
    seen: list[int | None] = []

    async def sleep(ms: float) -> None:
        slept.append(ms)

    async def op(attempt: int, ctx: RetryContext) -> str:
        seen.append(ctx.max_tokens_override)
        if attempt == 1:
            raise FakeError(
                "input length and `max_tokens` exceed context limit: 180000 + 40000 > 200000",
                status=400,
            )
        return "ok"

    assert _run(with_retry(op, _options(sleep=sleep))) == "ok"
    assert slept == []
    assert seen == [None, 200000 - 180000 - CONTEXT_OVERFLOW_SAFETY_BUFFER]


def test_an_overflow_with_no_room_left_raises_the_original_error_unwrapped():
    async def op(attempt: int, ctx: RetryContext) -> str:
        raise FakeError(
            "input length and `max_tokens` exceed context limit: 199000 + 40000 > 200000",
            status=400,
        )

    with pytest.raises(FakeError):
        _run(with_retry(op, _options()))


def test_the_overflow_budget_never_falls_below_the_thinking_budget():
    seen: list[int | None] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        seen.append(ctx.max_tokens_override)
        if attempt == 1:
            raise FakeError(
                "input length and `max_tokens` exceed context limit: 180000 + 40000 > 200000",
                status=400,
            )
        return "ok"

    _run(with_retry(op, _options(thinking_budget_tokens=50_000)))
    assert seen[1] == 50_001


def test_cancellation_stops_before_the_next_attempt():
    attempts: list[int] = []
    cancelled = {"value": False}

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        cancelled["value"] = True
        raise FakeError(status=500)

    with pytest.raises(CannotRetryError) as excinfo:
        _run(with_retry(op, _options(is_cancelled=lambda: cancelled["value"])))
    assert attempts == [1]
    # The refusal carries what failed and the context the next attempt would have used.
    assert isinstance(excinfo.value.original_error, FakeError)
    assert excinfo.value.context is not None and excinfo.value.context.model == "m"


def test_signals_raised_by_the_operation_itself_pass_straight_through():
    async def fallback_op(attempt: int, ctx: RetryContext) -> str:
        raise FallbackTriggeredError("a", "b")

    with pytest.raises(FallbackTriggeredError):
        _run(with_retry(fallback_op, _options()))

    async def final_op(attempt: int, ctx: RetryContext) -> str:
        raise CannotRetryError(ValueError("final"))

    with pytest.raises(CannotRetryError):
        _run(with_retry(final_op, _options()))


def test_the_default_sleep_actually_waits_in_milliseconds():
    import time

    async def op(attempt: int, ctx: RetryContext) -> str:
        if attempt == 1:
            raise FakeError(status=500, headers={"retry-after": "0"})
        return "ok"

    started = time.monotonic()
    assert _run(with_retry(op, RetryOptions(model="m", random=lambda: 0.0))) == "ok"
    assert time.monotonic() - started < 1.0


# ── the lookups, exercised at every branch ──────────────────────────────────

@pytest.mark.parametrize("attr", ["status_code", "status", "http_status"])
def test_every_status_attribute_name_is_consulted(attr):
    err = Exception("x")
    setattr(err, attr, 503)
    assert error_status(err) == 503
    assert should_retry(err) is True


@pytest.mark.parametrize("attr", ["status_code", "status", "http_status"])
def test_a_status_on_the_response_object_is_found_too(attr):
    class WithResponse(Exception):
        pass

    err = WithResponse("x")
    response = type("R", (), {})()
    setattr(response, attr, 429)
    err.response = response  # type: ignore[attr-defined]
    assert error_status(err) == 429


def test_the_search_keeps_looking_past_a_holder_that_has_nothing():
    """The error itself carries no status, so the response must still be consulted."""
    class WithResponse(Exception):
        pass

    err = WithResponse("x")
    err.response = type("R", (), {"status_code": 500})()  # type: ignore[attr-defined]
    assert error_status(err) == 500


def test_a_header_only_on_the_response_is_still_found():
    class WithResponse(Exception):
        pass

    err = WithResponse("x")
    err.response = type("R", (), {"headers": {"retry-after": "9"}})()  # type: ignore[attr-defined]
    assert get_retry_after(err) == "9"


def test_the_header_search_keeps_looking_past_an_empty_header_bag():
    """The first holder has headers but not this one, so the second must be consulted."""
    class WithResponse(Exception):
        pass

    err = WithResponse("x")
    err.headers = {"content-type": "application/json"}  # type: ignore[attr-defined]
    err.response = type("R", (), {"headers": {"retry-after": "11"}})()  # type: ignore[attr-defined]
    assert get_retry_after(err) == "11"


def test_a_header_bag_with_no_get_and_no_mapping_is_skipped():
    err = FakeError(headers=object())
    assert get_retry_after(err) is None


def test_a_header_getter_returning_none_falls_through():
    err = FakeError(headers=HeaderBag({}))
    assert get_retry_after(err) is None


@pytest.mark.parametrize("class_name", [
    "APIConnectionError", "APITimeoutError", "APIConnectionTimeoutError",
    "ConnectError", "ConnectTimeout", "ReadTimeout", "RemoteProtocolError",
])
def test_every_connection_error_name_is_recognised(class_name):
    error = type(class_name, (Exception,), {})()
    assert is_connection_error(error) is True
    assert should_retry(error) is True


def test_an_unrelated_class_name_is_not_a_connection_error():
    assert is_connection_error(type("ValueError2", (Exception,), {})()) is False


# ── the default sleep ───────────────────────────────────────────────────────

def test_the_default_sleep_treats_its_argument_as_milliseconds():
    import time

    from app.services.api.with_retry import _default_sleep

    started = time.monotonic()
    asyncio.run(_default_sleep(200))
    elapsed = time.monotonic() - started
    assert 0.15 < elapsed < 1.0, elapsed


def test_an_overflow_leaving_exactly_the_floor_is_still_recoverable():
    """The boundary: `available < FLOOR` raises, `available == FLOOR` retries."""
    seen: list[int | None] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        seen.append(ctx.max_tokens_override)
        if attempt == 1:
            raise FakeError(
                "input length and `max_tokens` exceed context limit: 196000 + 40000 > 200000",
                status=400,
            )
        return "ok"

    assert _run(with_retry(op, _options())) == "ok"
    assert seen == [None, FLOOR_OUTPUT_TOKENS]


def test_one_token_below_the_floor_is_unrecoverable():
    async def op(attempt: int, ctx: RetryContext) -> str:
        raise FakeError(
            "input length and `max_tokens` exceed context limit: 196001 + 40000 > 200000",
            status=400,
        )

    with pytest.raises(FakeError):
        _run(with_retry(op, _options()))


def test_an_overflow_retry_advances_the_attempt_counter():
    """Without the advance the loop would re-run attempt 1 forever on a repeating overflow."""
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        if len(attempts) < 3:
            raise FakeError(
                "input length and `max_tokens` exceed context limit: 180000 + 40000 > 200000",
                status=400,
            )
        return "ok"

    assert _run(with_retry(op, _options())) == "ok"
    assert attempts == [1, 2, 3]


def test_a_repeating_overflow_still_hits_the_ceiling():
    attempts: list[int] = []

    async def op(attempt: int, ctx: RetryContext) -> str:
        attempts.append(attempt)
        raise FakeError(
            "input length and `max_tokens` exceed context limit: 180000 + 40000 > 200000",
            status=400,
        )

    with pytest.raises(CannotRetryError):
        _run(with_retry(op, _options(max_retries=3)))
    assert attempts == [1, 2, 3, 4]
