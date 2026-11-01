"""
A single-consumer async queue, iterated once.

Ported from `reference/claude-code/src/utils/stream.ts` -> `Stream`: `enqueue` hands a value to a
waiting reader or queues it, `done` ends the iteration once the queue is drained, `error` fails the
next read. `StructuredIO.outbound` is one: every stdout message, control requests included, goes
through it in order.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Stream(Generic[T]):
    """`Stream<T>`."""

    def __init__(self, returned: Callable[[], None] | None = None) -> None:
        self._queue: deque[T] = deque()
        self._waiter: asyncio.Future[None] | None = None
        self._is_done = False
        self._error: BaseException | None = None
        self._started = False
        self._returned = returned

    def __aiter__(self) -> AsyncIterator[T]:
        if self._started:
            raise RuntimeError("Stream can only be iterated once")
        self._started = True
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[T]:
        try:
            while True:
                if self._queue:
                    yield self._queue.popleft()
                    continue
                if self._is_done:
                    return
                if self._error is not None:
                    raise self._error
                self._waiter = asyncio.get_running_loop().create_future()
                try:
                    await self._waiter
                finally:
                    self._waiter = None
        finally:
            # `return()`: the consumer stopped early.
            self._is_done = True
            if self._returned is not None:
                self._returned()

    def _wake(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    def enqueue(self, value: T) -> None:
        self._queue.append(value)
        self._wake()

    def done(self) -> None:
        self._is_done = True
        self._wake()

    def error(self, error: BaseException) -> None:
        self._error = error
        self._wake()


__all__ = ["Stream"]
