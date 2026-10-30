"""
Running a coroutine from synchronous code: the entry point drives the async session this way.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable
from typing import TypeVar

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


__all__ = ["run_sync"]
