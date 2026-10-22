"""
A hook's child process, captured while it runs.

The Python side of upstream's `wrapSpawn` / `ShellCommand` / `TaskOutput` as hooks use them
(`reference/claude-code/src/utils/hooks.ts` -> `execCommandHook`; `src/utils/ShellCommand.ts`):
stdout and stderr are drained into buffers for as long as the process lives, so a hook that
was backgrounded keeps being captured after its caller moved on, and its output can be
collected when it finishes.

Cancellation is cooperative on this side: the run's `CancelToken` has no listeners, so the
wait polls it, and an aborted or timed-out hook is killed — upstream's combined abort signal
does the same to the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal as signal_module
from typing import Any

#: How often a wait re-checks the cancel signal.
ABORT_POLL_SECONDS = 0.05


def is_aborted(signal: Any) -> bool:
    """An `AbortSignal` (`aborted`) or a `CancelToken` (`cancelled`) that has fired."""
    if signal is None:
        return False
    return bool(getattr(signal, "aborted", False) or getattr(signal, "cancelled", False))


class CapturedProcess:
    """A spawned hook process whose output is drained into memory until it exits."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.stdout = ""
        self.stderr = ""
        self.output = ""
        self.status: str = "running"
        self.exit_code: int | None = None
        self.stdout_changed = asyncio.Event()
        self._readers = [
            asyncio.ensure_future(self._drain(process.stdout, "stdout")),
            asyncio.ensure_future(self._drain(process.stderr, "stderr")),
        ]
        self._done = asyncio.ensure_future(self._wait())

    async def _drain(self, stream: asyncio.StreamReader | None, name: str) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            self.output += text
            if name == "stdout":
                self.stdout += text
                self.stdout_changed.set()
            else:
                self.stderr += text

    async def _wait(self) -> int:
        code = await self.process.wait()
        await asyncio.gather(*self._readers, return_exceptions=True)
        self.exit_code = code if code is not None else 1
        if self.status == "running":
            self.status = "completed"
        self.stdout_changed.set()
        return self.exit_code

    @property
    def done(self) -> asyncio.Future[int]:
        return self._done

    async def write_stdin(self, data: str) -> None:
        stdin = self.process.stdin
        if stdin is None:
            return
        stdin.write(data.encode("utf-8"))
        with contextlib.suppress(Exception):
            await stdin.drain()
        stdin.close()

    def kill(self) -> None:
        """
        Kill the child and everything it started (upstream's `treeKill`); the capture is then
        `killed`, as `ShellCommand.kill` marks it. The child leads its own process group
        (`start_new_session`), so the group is the tree; a grandchild left alive would hold
        the pipes open and the wait would hang on it.
        """
        if self.status == "running":
            self.status = "killed"
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal_module.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            self.process.kill()

    async def result(self) -> int:
        return await self._done


__all__ = ["ABORT_POLL_SECONDS", "CapturedProcess", "is_aborted"]
