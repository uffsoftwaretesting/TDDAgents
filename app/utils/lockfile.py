"""
Cross-process advisory locks: a `<file>.lock` directory, kept fresh while held.

Ported from `reference/claude-code/src/utils/lockfile.ts` -> `lock`, a lazy wrapper over the
`proper-lockfile` package, whose algorithm is reproduced here (the package is not part of the
source snapshot; its documented defaults are cited):

* the lock is the directory `<file>.lock`, created with `mkdir` (atomic on every filesystem);
* an existing lock whose mtime is older than `stale` (default 10 000 ms) is stale: it is
  removed and the acquisition retried once; a fresh one fails with code `ELOCKED`;
* while held, the lock's mtime is refreshed every `update` ms (default `stale / 2`); a refresh
  that fails marks the lock compromised and calls `on_compromised` (the package's default
  throws; every caller here passes a handler);
* the release function stops the refresh and removes the directory.

`realpath` is accepted for upstream's call shape: with `False` (the only value TDDAgents
passes) the file need not exist, which is also what this implementation does.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

#: `proper-lockfile`'s `stale` default (ms).
DEFAULT_STALE_MS = 10_000


class LockError(OSError):
    """A lock failure carrying `proper-lockfile`'s error `code` (`ELOCKED`, ...)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def get_lock_path(file: str | os.PathLike[str]) -> Path:
    """`getLockFile`: the lock directory beside `file`."""
    return Path(f"{os.fspath(file)}.lock")


def _try_mkdir(lock_path: Path, stale_ms: int, *, allow_stale_retry: bool) -> None:
    try:
        lock_path.mkdir()
        return
    except FileExistsError:
        pass
    try:
        mtime = lock_path.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    if allow_stale_retry and (mtime is None or (time.time() - mtime) * 1000 > stale_ms):
        with contextlib.suppress(FileNotFoundError):
            lock_path.rmdir()
        _try_mkdir(lock_path, stale_ms, allow_stale_retry=False)
        return
    raise LockError("Lock file is already being held", "ELOCKED")


async def lock(
    file: str | os.PathLike[str],
    *,
    stale: int = DEFAULT_STALE_MS,
    update: int | None = None,
    realpath: bool = True,
    on_compromised: Callable[[Exception], None] | None = None,
) -> Callable[[], Awaitable[None]]:
    """`lock`: acquire `<file>.lock` or raise `LockError`; returns the async release function."""
    del realpath  # see the module docstring
    lock_path = get_lock_path(file)
    _try_mkdir(lock_path, stale, allow_stale_retry=True)
    interval_s = (update if update is not None else stale / 2) / 1000

    async def refresh() -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                os.utime(lock_path)
            except OSError as exc:
                if on_compromised is not None:
                    on_compromised(exc)
                return

    refresher = asyncio.get_running_loop().create_task(refresh())
    released = False

    async def release() -> None:
        nonlocal released
        if released:
            raise LockError("Lock is already released", "ERELEASED")
        released = True
        refresher.cancel()
        await asyncio.gather(refresher, return_exceptions=True)
        lock_path.rmdir()

    return release


__all__ = ["DEFAULT_STALE_MS", "LockError", "get_lock_path", "lock"]
