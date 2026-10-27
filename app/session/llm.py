"""
The session shell's model calls, under the loop's one retry policy (Phase 9).

The analyst, engineer and planner are single LLM calls made from synchronous LangGraph
nodes. Each goes through `app/services/api/with_retry.py::with_retry` — the port of `withRetry.ts`,
and the one ceiling in the codebase — so the shell and the loop retry the same errors with
the same backoff, instead of the old graph's three attempts and fixed 3 s sleep.

A refused or exhausted retry raises `CannotRetryError`, which the node turns into a failed
session; a later run with the same thread id resumes from the last checkpoint.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.config.config import Config
from app.services.api.with_retry import RetryContext, RetryOptions, with_retry

T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    """Run a coroutine from synchronous code, in a worker thread when a loop is already running."""

    async def _runner() -> T:
        return await coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_runner())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _runner()).result()


def call_with_retry(
    fn: Callable[..., T],
    *args: Any,
    model: str = Config.MODEL,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """`fn(*args)` retried by `with_retry` for `model`; the value of the first success."""

    async def operation(_attempt: int, _context: RetryContext) -> T:
        return fn(*args)

    return run_sync(with_retry(operation, RetryOptions(model=model, sleep=sleep)))


__all__ = ["call_with_retry", "run_sync"]
