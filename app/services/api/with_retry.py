"""
The retry layer around the model call (Part F6 / Phase 1).

Ported from `reference/claude-code/src/services/api/withRetry.ts` -> `withRetry`,
`getRetryDelay`, `shouldRetry`, `FallbackTriggeredError`, `CannotRetryError`.

Every constant below is upstream's, with its name kept, because a retry schedule invented
locally is a schedule nobody can check against the system it imitates. **This is the one
place in this codebase that has a ceiling**: `DEFAULT_MAX_RETRIES` attempts, exactly as
upstream, because a provider that never recovers would otherwise hang a run with no exit.
The no-ceilings rule still holds everywhere upstream leaves something unbounded — turns
above all. See `CLAUDE.md`, "Ceilings follow claude-code, nothing else".

Two forced divergences, both recorded rather than hidden:

* **`with_retry` is a coroutine with an `on_notice` callback, not an async generator.**
  Upstream's `withRetry` is `AsyncGenerator<SystemAPIErrorMessage, T>` — it yields retry
  notices *and returns the operation's value*. PEP 525 forbids a value in an async
  generator's `return`, the same constraint `run_loop`/`drain` already works around.
* **No subscriber tiers, mock-injection or persistent/unattended mode.** Those branches
  key on upstream's own account model and feature flags, neither of which exists here.
  What is left is the shape every provider shares: status codes, `retry-after`,
  `x-should-retry`, connection failures and the 529 fallback trigger.
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: `withRetry.ts` -> `DEFAULT_MAX_RETRIES`. Attempts are `max_retries + 1`.
DEFAULT_MAX_RETRIES = 10

#: `withRetry.ts` -> `BASE_DELAY_MS`: the first backoff, doubled each attempt.
BASE_DELAY_MS = 500

#: `getRetryDelay`'s default `maxDelayMs`. The cap applies *before* jitter, so the longest
#: real sleep is `DEFAULT_MAX_DELAY_MS * (1 + JITTER_FACTOR)`.
DEFAULT_MAX_DELAY_MS = 32_000

#: `getRetryDelay`'s jitter: additive and one-sided, so the delay lands in
#: `[base, base * 1.25)`. Not symmetric and not full jitter — upstream's exact shape.
JITTER_FACTOR = 0.25

#: `withRetry.ts` -> `MAX_529_RETRIES`: consecutive overloads before the fallback fires.
MAX_529_RETRIES = 3

#: `withRetry.ts` -> `FLOOR_OUTPUT_TOKENS`: below this an overflow is unrecoverable.
FLOOR_OUTPUT_TOKENS = 3000

#: The `safetyBuffer` subtracted when recomputing an output budget from a context overflow.
CONTEXT_OVERFLOW_SAFETY_BUFFER = 1000

#: `shouldRetry`'s marker for an overload the SDK reported without a status code.
OVERLOADED_ERROR_MARKER = '"type":"overloaded_error"'

_REVOKED_OAUTH = "OAuth token has been revoked"

#: `parseMaxTokensContextOverflowError`: "input length and `max_tokens` exceed context limit: A + B > C".
_CONTEXT_OVERFLOW = re.compile(
    r"input length and `max_tokens` exceed context limit:\s*(\d+)\s*\+\s*(\d+)\s*>\s*(\d+)"
)

_LEADING_INT = re.compile(r"\s*([+-]?[0-9]+)")


class CannotRetryError(Exception):
    """
    The failure is final: not retryable, or the attempt ceiling was reached.

    Upstream names the class `CannotRetryError` but sets `this.name = 'RetryError'`; the
    two deliberately differ, and nothing reads the name, so the port keeps one spelling.
    """

    def __init__(self, original_error: BaseException | None, context: RetryContext | None = None) -> None:
        super().__init__(str(original_error) if original_error is not None else "retries exhausted")
        self.original_error = original_error
        self.context = context


class FallbackTriggeredError(Exception):
    """
    A signal, not a failure: switch to the fallback model and retry the whole request.

    Carries no original error, exactly as upstream — the caller that catches it performs
    the model switch, and swallowing it would turn the fallback into a no-op.
    """

    def __init__(self, original_model: str, fallback_model: str) -> None:
        super().__init__(f"Model fallback triggered: {original_model} -> {fallback_model}")
        self.original_model = original_model
        self.fallback_model = fallback_model


@dataclass(frozen=True, slots=True)
class RetryContext:
    """`RetryOptions`' mutable companion: what the next attempt should do differently."""

    model: str
    max_tokens_override: int | None = None


@dataclass(frozen=True, slots=True)
class RetryNotice:
    """
    One "retrying in N ms" event. Upstream yields a `SystemAPIErrorMessage`; here it goes
    to a callback, because an async generator cannot also return the operation's value.
    """

    error: BaseException
    delay_ms: float
    attempt: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class RetryOptions:
    """`RetryOptions`, minus the branches that key on upstream's account model."""

    model: str
    max_retries: int = DEFAULT_MAX_RETRIES
    fallback_model: str | None = None
    initial_consecutive_529_errors: int = 0
    on_notice: Callable[[RetryNotice], None] | None = None
    thinking_budget_tokens: int = 0
    is_cancelled: Callable[[], bool] | None = None
    sleep: Callable[[float], Awaitable[None]] | None = None
    random: Callable[[], float] = field(default=random.random)


def js_parse_int(value: str | None) -> int | None:
    """
    `parseInt(value, 10)`: leading whitespace and sign, then digits, trailing junk ignored.

    `None` stands in for `NaN`, which is the only thing either caller tests for.
    """
    match = _LEADING_INT.match(value or "")
    return int(match.group(1)) if match is not None else None


def _header(error: BaseException, name: str) -> str | None:
    """
    `getRetryAfter`'s two-shape header read: a plain mapping and a `Headers`-like object.

    Upstream reads `headers['retry-after'] || headers.get?.('retry-after')` because the SDK
    hands back both shapes depending on where the error was raised.
    """
    for holder in (error, getattr(error, "response", None)):
        headers = getattr(holder, "headers", None)
        if headers is None:
            continue
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if str(key).lower() == name:
                    return str(value)
        getter = getattr(headers, "get", None)
        if callable(getter):
            try:
                found = getter(name)
            except Exception:  # noqa: BLE001  # pragma: no cover - a header bag that raises is not a retry signal
                found = None
            if found is not None:
                return str(found)
    return None


def get_retry_after(error: BaseException) -> str | None:
    """`getRetryAfter`: the raw `retry-after` header, in whichever shape it arrived."""
    return _header(error, "retry-after")


def error_status(error: BaseException) -> int | None:
    """The HTTP status, read off whichever attribute the provider's exception carries."""
    for holder in (error, getattr(error, "response", None)):
        for attr in ("status_code", "status", "http_status"):
            value = getattr(holder, attr, None)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            return value
    return None


def is_connection_error(error: BaseException) -> bool:
    """`error instanceof APIConnectionError`, as a duck-typed predicate."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    return type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "APIConnectionTimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "RemoteProtocolError",
    }


def is_529_error(error: BaseException) -> bool:
    """
    `is529Error`: status 529, **or** the overloaded marker in the message.

    The string check is not redundant: the SDK drops the status while streaming, which is
    exactly when an overload is most likely to be seen.
    """
    return error_status(error) == 529 or OVERLOADED_ERROR_MARKER in str(error)


def parse_max_tokens_context_overflow(error: BaseException) -> tuple[int, int, int] | None:
    """`parseMaxTokensContextOverflowError` -> `(input_tokens, max_tokens, context_limit)`."""
    if error_status(error) != 400:
        return None
    match = _CONTEXT_OVERFLOW.search(str(error))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def should_retry(error: BaseException) -> bool:
    """
    `shouldRetry`, in upstream's evaluation order, minus the subscriber-tier branches.

    Order matters: `x-should-retry` is consulted before the status table, so a provider can
    veto a retry on a status this would otherwise retry.
    """
    if OVERLOADED_ERROR_MARKER in str(error):
        return True

    if parse_max_tokens_context_overflow(error) is not None:
        return True

    should = (_header(error, "x-should-retry") or "").strip().lower()
    if should == "true":
        return True
    if should == "false":
        return False

    if is_connection_error(error):
        return True

    status = error_status(error)
    if status is None:
        return False
    if status in (408, 409, 429, 401):
        return True
    if status == 403 and _REVOKED_OAUTH in str(error):
        return True
    return status >= 500


def get_retry_delay(
    attempt: int,
    retry_after_header: str | None = None,
    max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
    rand: Callable[[], float] = random.random,
) -> float:
    """
    `getRetryDelay`. `attempt` is 1-based and names the attempt that just failed.

    A valid `retry-after` returns immediately: it is a server directive, so it bypasses
    both `max_delay_ms` and the jitter. Upstream notes that this is intentional.
    """
    if retry_after_header:
        seconds = js_parse_int(retry_after_header)
        if seconds is not None:
            return seconds * 1000

    # `int ** int` is `Any` to mypy, because a negative exponent would make it a float.
    doublings: int = int(2 ** (attempt - 1))
    base_delay = min(BASE_DELAY_MS * doublings, max_delay_ms)
    return base_delay + rand() * JITTER_FACTOR * base_delay


async def _default_sleep(delay_ms: float) -> None:
    import asyncio

    await asyncio.sleep(delay_ms / 1000)


async def with_retry(
    operation: Callable[[int, RetryContext], Awaitable[T]],
    options: RetryOptions,
) -> T:
    """
    Run `operation` until it succeeds, is refused, or the attempt ceiling is reached.

    `operation(attempt, context)` is called with a 1-based attempt number and the context
    carrying anything the previous failure asked the next attempt to change.

    Raises `FallbackTriggeredError` (switch model and retry the whole request),
    `CannotRetryError` (final), or the original error where upstream rethrows it raw.
    """
    max_retries = options.max_retries
    sleep = options.sleep or _default_sleep
    consecutive_529 = options.initial_consecutive_529_errors
    context = RetryContext(model=options.model)
    last_error: BaseException | None = None

    attempt = 1
    while attempt <= max_retries + 1:
        if options.is_cancelled is not None and options.is_cancelled():
            raise CannotRetryError(last_error, context)

        try:
            return await operation(attempt, context)
        except (CannotRetryError, FallbackTriggeredError):
            raise
        except Exception as error:
            last_error = error

            if is_529_error(error):
                consecutive_529 += 1
                if consecutive_529 >= MAX_529_RETRIES and options.fallback_model:
                    logger.info(
                        "Falling back from %s to %s after %d consecutive overloads",
                        options.model, options.fallback_model, consecutive_529,
                    )
                    raise FallbackTriggeredError(options.model, options.fallback_model) from error

            if attempt > max_retries:
                raise CannotRetryError(error, context) from error

            if not should_retry(error):
                raise CannotRetryError(error, context) from error

            overflow = parse_max_tokens_context_overflow(error)
            if overflow is not None:
                input_tokens, _requested, context_limit = overflow
                available = max(0, context_limit - input_tokens - CONTEXT_OVERFLOW_SAFETY_BUFFER)
                if available < FLOOR_OUTPUT_TOKENS:
                    # Upstream rethrows raw here: there is no output budget left to shrink to.
                    raise
                context = replace(
                    context,
                    max_tokens_override=max(
                        FLOOR_OUTPUT_TOKENS, available, options.thinking_budget_tokens + 1
                    ),
                )
                # Retried immediately, without sleeping: nothing is overloaded, the ask was.
                attempt += 1
                continue

            delay_ms = get_retry_delay(attempt, get_retry_after(error), rand=options.random)
            if options.on_notice is not None:
                options.on_notice(
                    RetryNotice(error=error, delay_ms=delay_ms, attempt=attempt, max_retries=max_retries)
                )
            logger.info(
                "Attempt %d/%d failed (%s); retrying in %.0f ms",
                attempt, max_retries + 1, type(error).__name__, delay_ms,
            )
            await sleep(delay_ms)
            attempt += 1

    raise CannotRetryError(last_error, context)


__all__ = [
    "BASE_DELAY_MS",
    "CONTEXT_OVERFLOW_SAFETY_BUFFER",
    "DEFAULT_MAX_DELAY_MS",
    "DEFAULT_MAX_RETRIES",
    "FLOOR_OUTPUT_TOKENS",
    "JITTER_FACTOR",
    "MAX_529_RETRIES",
    "OVERLOADED_ERROR_MARKER",
    "CannotRetryError",
    "FallbackTriggeredError",
    "RetryContext",
    "RetryNotice",
    "RetryOptions",
    "error_status",
    "get_retry_after",
    "get_retry_delay",
    "is_529_error",
    "is_connection_error",
    "js_parse_int",
    "parse_max_tokens_context_overflow",
    "should_retry",
    "with_retry",
]
