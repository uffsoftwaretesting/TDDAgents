"""
Shell child processes: a hook's (pipe mode) and the Bash tool's (file mode).

The Python side of upstream's `wrapSpawn` / `ShellCommand` / `TaskOutput` as hooks use them
(`reference/claude-code/src/utils/hooks.ts` -> `execCommandHook`; `src/utils/ShellCommand.ts`):
stdout and stderr are drained into buffers for as long as the process lives, so a hook that
was backgrounded keeps being captured after its caller moved on, and its output can be
collected when it finishes.

Cancellation is cooperative on this side: the run's `CancelToken` has no listeners, so the
wait polls it, and an aborted or timed-out hook is killed — upstream's combined abort signal
does the same to the child.

The Bash tool's `ShellCommand` is upstream's file mode (`ShellCommandImpl`, `wrapSpawn`,
`createAbortedCommand`, `createFailedCommand`): output goes to the task's file, not to pipes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal as signal_module
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.utils.format import format_duration
from app.utils.task.disk_output import (
    MAX_TASK_OUTPUT_BYTES,
    MAX_TASK_OUTPUT_BYTES_DISPLAY,
)

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


# ── The Bash tool's shell command (file mode) ───────────────────────────────

#: `ShellCommand.ts`: the exit codes of a killed and of a timed-out command.
SIGKILL_EXIT_CODE = 137
SIGTERM_EXIT_CODE = 143
#: `SIZE_WATCHDOG_INTERVAL_MS`, in seconds.
SIZE_WATCHDOG_INTERVAL_SECONDS = 5.0
#: `AbortedShellCommand`'s default code.
ABORTED_EXIT_CODE = 145

ShellStatus = Literal["running", "backgrounded", "completed", "killed"]


@dataclass
class ExecResult:
    """`ExecResult`."""

    code: int
    stdout: str
    stderr: str
    interrupted: bool
    background_task_id: str | None = None
    output_file_path: str | None = None
    output_file_size: int | None = None
    output_task_id: str | None = None
    pre_spawn_error: str | None = None


def prepend_stderr(prefix: str, stderr: str) -> str:
    """`prependStderr`."""
    return f"{prefix} {stderr}" if stderr else prefix


class ShellCommand:
    """
    `ShellCommandImpl` over a `ShellProcess` (`app/workspace/base.py`): the command's output is in
    its `TaskOutput` file, and the result comes when the shell *exits* — never when its output
    closes — so `cd x && server &` returns while the server keeps running.

    A timeout either backgrounds the command (when auto-backgrounding is allowed and a callback
    is registered with `on_timeout`) or kills it with code 143. `background(task_id)` detaches it
    from the caller: the timeout is cleared and the size watchdog kills it if its output file
    passes `max_output_bytes`. `kill()` ends it (and everything it started) with code 137.
    """

    def __init__(
        self,
        process: Any,
        task_output: Any,
        timeout_ms: float,
        should_auto_background: bool = False,
        max_output_bytes: int = MAX_TASK_OUTPUT_BYTES,
        on_exit: Callable[[ExecResult], None] | None = None,
    ) -> None:
        self.task_output = task_output
        self._process = process
        self._status: ShellStatus = "running"
        self._timeout_ms = timeout_ms
        self._should_auto_background = should_auto_background
        self._max_output_bytes = max_output_bytes
        self._on_exit = on_exit
        self._on_timeout_callback: Callable[[Callable[[str], bool]], None] | None = None
        self._background_task_id: str | None = None
        self._killed_for_size = False
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._timer: asyncio.TimerHandle | None = asyncio.get_running_loop().call_later(
            timeout_ms / 1000, self._handle_timeout)
        self._watchdog: asyncio.Task[None] | None = None
        self._waiter = asyncio.ensure_future(self._wait_for_process())
        self.result: asyncio.Future[ExecResult] = asyncio.ensure_future(self._handle_exit())

    @property
    def status(self) -> ShellStatus:
        return self._status

    def on_timeout(self, callback: Callable[[Callable[[str], bool]], None]) -> None:
        """`onTimeout`: on timeout, `callback(background)` runs instead of the kill."""
        self._on_timeout_callback = callback

    async def _wait_for_process(self) -> None:
        self._resolve_exit(await self._process.wait())

    def _resolve_exit(self, code: int) -> None:
        if not self._exit.done():
            self._exit.set_result(code)

    def _handle_timeout(self) -> None:
        """`#handleTimeout`."""
        self._timer = None
        if self._should_auto_background and self._on_timeout_callback is not None:
            self._on_timeout_callback(self.background)
        else:
            self._do_kill(SIGTERM_EXIT_CODE)

    def _cleanup_listeners(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    async def _handle_exit(self) -> ExecResult:
        """`#handleExit`: the result, read from the output file."""
        code = await self._exit
        self._cleanup_listeners()
        if self._status in ("running", "backgrounded"):
            self._status = "completed"
        result = ExecResult(
            code=code,
            stdout=self.task_output.get_stdout(),
            stderr=self.task_output.get_stderr(),
            interrupted=code == SIGKILL_EXIT_CODE,
            background_task_id=self._background_task_id,
        )
        if not self._background_task_id:
            if self.task_output.output_file_redundant:
                self.task_output.delete_output_file()
            else:
                result.output_file_path = str(self.task_output.path)
                result.output_file_size = self.task_output.output_file_size
                result.output_task_id = self.task_output.task_id
        if self._killed_for_size:
            result.stderr = prepend_stderr(
                f"Background command killed: output file exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY}", result.stderr)
        elif code == SIGTERM_EXIT_CODE:
            result.stderr = prepend_stderr(f"Command timed out after {format_duration(self._timeout_ms)}",
                                           result.stderr)
        if self._on_exit is not None:
            self._on_exit(result)
        return result

    def _do_kill(self, code: int = SIGKILL_EXIT_CODE) -> None:
        """`#doKill`: killed, the whole tree, and the exit code resolved at once."""
        self._status = "killed"
        self._process.kill_tree()
        self._resolve_exit(code)

    def kill(self) -> None:
        self._do_kill()

    async def _size_watchdog(self) -> None:
        """`#startSizeWatchdog`: kill a backgrounded command whose output file grew too large."""
        while True:
            await asyncio.sleep(SIZE_WATCHDOG_INTERVAL_SECONDS)
            try:
                size = os.path.getsize(self.task_output.path)
            except OSError:
                continue
            if size > self._max_output_bytes:
                self._killed_for_size = True
                self._do_kill()
                return

    def background(self, task_id: str) -> bool:
        """`background`: only a running command can be backgrounded."""
        if self._status != "running":
            return False
        self._background_task_id = task_id
        self._status = "backgrounded"
        self._cleanup_listeners()
        self._watchdog = asyncio.ensure_future(self._size_watchdog())
        return True

    def cleanup(self) -> None:
        self._cleanup_listeners()


class FinishedShellCommand:
    """`createAbortedCommand` / `createFailedCommand`: a command that never ran."""

    def __init__(self, result: ExecResult, status: ShellStatus, task_output: Any) -> None:
        self.task_output = task_output
        self.status: ShellStatus = status
        self.result: asyncio.Future[ExecResult] = asyncio.get_running_loop().create_future()
        self.result.set_result(result)

    def on_timeout(self, callback: Callable[[Callable[[str], bool]], None]) -> None:
        return None

    def background(self, task_id: str) -> bool:
        return False

    def kill(self) -> None:
        return None

    def cleanup(self) -> None:
        return None


def create_aborted_command(task_output: Any) -> FinishedShellCommand:
    """`createAbortedCommand`: code 145, interrupted."""
    result = ExecResult(code=ABORTED_EXIT_CODE, stdout="", stderr="Command aborted before execution", interrupted=True)
    return FinishedShellCommand(result, "killed", task_output)


def create_failed_command(pre_spawn_error: str, task_output: Any) -> FinishedShellCommand:
    """`createFailedCommand`: the shell could not be started."""
    result = ExecResult(code=1, stdout="", stderr=pre_spawn_error, interrupted=False, pre_spawn_error=pre_spawn_error)
    return FinishedShellCommand(result, "completed", task_output)
