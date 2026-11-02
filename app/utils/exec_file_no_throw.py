"""
Run a program without a shell and never raise: the result carries the exit code and output.

Ported from `reference/claude-code/src/utils/execFileNoThrow.ts` -> `execFileNoThrow`,
`execFileNoThrowWithCwd`, `getErrorMessage`.

Upstream runs through execa with `reject: false`; here `asyncio.create_subprocess_exec`, with
execa's defaults where they matter: `env` extends the process environment (`extendEnv`), one
final newline is stripped from each stream (`stripFinalNewline`), and the timeout (upstream's
default, ten minutes) kills the process and returns at once with the output read so far, even
when a grandchild still holds the pipes. A failed run's `error` is execa's `shortMessage`
(`Command failed with exit code N: <command>`, `Command timed out after N milliseconds:
<command>`, `Command was killed with SIGNAL: <command>`, `Command failed with ENOENT: <command>`
for a program or directory that does not exist); a run killed by a signal has code 1, as
`exitCode ?? 1`.

Divergences: `execFileNoThrow` (`useCwd`) runs in the process directory: this repository has no
process-wide working directory (`app/bootstrap/state.py`), so a caller that needs one passes
`cwd`. `maxBuffer`, `shell` and `abortSignal` are not ported. Without `input` the default stdin
is the null device rather than execa's open pipe (nothing here writes to it). The timeout
sends SIGKILL at once, where execa sends SIGTERM and SIGKILL five seconds later.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shlex
import signal as signal_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

MS_IN_SECOND = 1000
SECONDS_IN_MINUTE = 60
#: The default timeout, `10 * SECONDS_IN_MINUTE * MS_IN_SECOND`.
DEFAULT_TIMEOUT_MS = 10 * SECONDS_IN_MINUTE * MS_IN_SECOND
_READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ExecResult:
    """`{stdout, stderr, code, error?}`."""

    stdout: str
    stderr: str
    code: int
    error: str | None = None


def _short_message(command: str, code: int, timed_out: bool, timeout_ms: int) -> str:
    if timed_out:
        return f"Command timed out after {timeout_ms} milliseconds: {command}"
    if code < 0:
        try:
            name = signal_module.Signals(-code).name
        except ValueError:
            name = str(-code)
        return f"Command was killed with {name}: {command}"
    return f"Command failed with exit code {code}: {command}"


def _strip_final_newline(text: str) -> str:
    """execa's `stripFinalNewline`: one trailing `\\n` (or `\\r\\n`)."""
    if text.endswith("\r\n"):
        return text[:-2]
    return text.removesuffix("\n")


async def _drain(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
    if stream is None:
        return
    while True:
        data = await stream.read(_READ_CHUNK)
        if not data:
            return
        chunks.append(data)


async def _feed(stdin: asyncio.StreamWriter | None, data: bytes | None) -> None:
    if stdin is None:
        return
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        if data:
            stdin.write(data)
            await stdin.drain()
        stdin.close()


async def exec_file_no_throw_with_cwd(
    file: str,
    args: Sequence[str],
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    preserve_output_on_error: bool = True,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Literal["ignore", "inherit", "pipe"] | None = None,
    input: str | None = None,
) -> ExecResult:
    """`execFileNoThrowWithCwd`: always resolves, never raises."""
    command = shlex.join([file, *args])
    stdin_mode: int | None
    if input is not None or stdin == "pipe":
        stdin_mode = asyncio.subprocess.PIPE
    elif stdin == "inherit":
        stdin_mode = None
    else:
        stdin_mode = asyncio.subprocess.DEVNULL
    try:
        process = await asyncio.create_subprocess_exec(
            file,
            *args,
            stdin=stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, **env} if env is not None else None,
        )
    except OSError as exc:
        # execa resolves a spawn failure as a failed result (`reject: false`).
        if not preserve_output_on_error:
            return ExecResult(stdout="", stderr="", code=1)
        name = errno.errorcode.get(exc.errno or 0, str(exc))
        return ExecResult(stdout="", stderr="", code=1, error=f"Command failed with {name}: {command}")

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    readers = [
        asyncio.ensure_future(_drain(process.stdout, out_chunks)),
        asyncio.ensure_future(_drain(process.stderr, err_chunks)),
    ]
    feeder = asyncio.ensure_future(_feed(process.stdin, input.encode("utf-8") if input is not None else None))
    everything = asyncio.gather(*readers, feeder, process.wait())
    done, _ = await asyncio.wait({everything}, timeout=timeout_ms / MS_IN_SECOND)
    timed_out = not done
    if timed_out:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        # A grandchild may still hold the pipes: keep what was read and stop reading.
        for task in (*readers, feeder):
            task.cancel()
        await asyncio.gather(everything, return_exceptions=True)
    elif everything.exception() is not None:
        # A pipe failed while the program ran: kill it and report what was read.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
    code = process.returncode if process.returncode is not None else 1
    stdout = _strip_final_newline(b"".join(out_chunks).decode("utf-8", errors="replace"))
    stderr = _strip_final_newline(b"".join(err_chunks).decode("utf-8", errors="replace"))
    if timed_out or code != 0:
        error_code = code if code > 0 else 1
        if not preserve_output_on_error:
            return ExecResult(stdout="", stderr="", code=error_code)
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            code=error_code,
            error=_short_message(command, code, timed_out, timeout_ms),
        )
    return ExecResult(stdout=stdout, stderr=stderr, code=0)


async def exec_file_no_throw(
    file: str,
    args: Sequence[str],
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    preserve_output_on_error: bool = True,
    env: Mapping[str, str] | None = None,
    stdin: Literal["ignore", "inherit", "pipe"] | None = None,
    input: str | None = None,
) -> ExecResult:
    """`execFileNoThrow`: `exec_file_no_throw_with_cwd` in the process directory."""
    try:
        cwd: str | None = os.getcwd()
    except OSError:
        cwd = None
    return await exec_file_no_throw_with_cwd(
        file,
        args,
        timeout_ms=timeout_ms,
        preserve_output_on_error=preserve_output_on_error,
        cwd=cwd,
        env=env,
        stdin=stdin,
        input=input,
    )


__all__ = ["DEFAULT_TIMEOUT_MS", "ExecResult", "exec_file_no_throw", "exec_file_no_throw_with_cwd"]
