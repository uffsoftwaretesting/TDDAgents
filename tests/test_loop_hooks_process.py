"""Tests for `app/utils/hooks/hooks.py`: the captured hook child process."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from app.tool import CancelToken
from app.utils.abort_controller import AbortController
from app.utils.shell_command import ABORT_POLL_SECONDS, CapturedProcess, is_aborted


async def _spawn(command: str, **kwargs: Any) -> CapturedProcess:
    process = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        **kwargs,
    )
    return CapturedProcess(process)


def test_poll_interval_is_fifty_milliseconds():
    assert ABORT_POLL_SECONDS == 0.05


def test_is_aborted_reads_both_signal_shapes():
    assert is_aborted(None) is False
    controller = AbortController()
    assert is_aborted(controller.signal) is False
    controller.abort()
    assert is_aborted(controller.signal) is True
    token = CancelToken()
    assert is_aborted(token) is False
    token.cancel()
    assert is_aborted(token) is True
    assert is_aborted(SimpleNamespace()) is False
    assert is_aborted(SimpleNamespace(aborted=False, cancelled=True)) is True
    assert is_aborted(SimpleNamespace(aborted=True, cancelled=False)) is True


def test_capture_separates_and_interleaves_streams():
    async def go() -> CapturedProcess:
        cap = await _spawn("printf out1; printf err1 >&2; printf out2; exit 4")
        assert cap.status == "running"
        assert cap.exit_code is None
        await cap.write_stdin("")
        code = await cap.result()
        assert code == 4
        return cap

    cap = asyncio.run(go())
    assert cap.stdout == "out1out2"
    assert cap.stderr == "err1"
    assert sorted(cap.output) == sorted("out1out2err1")
    assert cap.status == "completed"
    assert cap.exit_code == 4
    assert cap.stdout_changed.is_set()
    assert cap.done.done()


def test_stdin_is_written_and_closed():
    async def go() -> CapturedProcess:
        cap = await _spawn("cat")
        await cap.write_stdin('{"x": 1}\n')
        await cap.result()
        return cap

    cap = asyncio.run(go())
    assert cap.stdout == '{"x": 1}\n'
    assert cap.exit_code == 0


def test_undecodable_bytes_are_replaced():
    async def go() -> CapturedProcess:
        cap = await _spawn("printf '\\377abc'")
        await cap.write_stdin("")
        await cap.result()
        return cap

    assert asyncio.run(go()).stdout == "�abc"


def test_stdout_change_is_signalled_before_exit():
    async def go() -> bool:
        cap = await _spawn("echo first; sleep 2")
        await cap.write_stdin("")
        await asyncio.wait_for(cap.stdout_changed.wait(), timeout=1.5)
        running = cap.status == "running"
        cap.kill()
        await cap.result()
        return running

    assert asyncio.run(go()) is True


def test_kill_takes_the_whole_process_group_including_a_grandchild():
    async def go() -> tuple[CapturedProcess, float]:
        # The grandchild `sleep` inherits the pipes; killing only the shell would leave
        # the wait hanging until it exits on its own.
        cap = await _spawn("echo started; sleep 5 & sleep 5; echo never")
        await cap.write_stdin("")
        await asyncio.wait_for(cap.stdout_changed.wait(), timeout=1.5)
        started = time.monotonic()
        cap.kill()
        await asyncio.wait_for(cap.result(), timeout=2)
        return cap, time.monotonic() - started

    cap, elapsed = asyncio.run(go())
    assert elapsed < 1.5
    assert cap.status == "killed"
    assert cap.stdout == "started\n"
    assert cap.exit_code is not None and cap.exit_code < 0


def test_kill_after_exit_keeps_completed_and_is_harmless():
    async def go() -> CapturedProcess:
        cap = await _spawn("true")
        await cap.write_stdin("")
        await cap.result()
        cap.kill()
        return cap

    cap = asyncio.run(go())
    assert cap.status == "completed"
    assert cap.exit_code == 0


def test_write_stdin_without_a_pipe_is_a_no_op():
    async def go() -> CapturedProcess:
        process = await asyncio.create_subprocess_shell(
            "echo hi", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        cap = CapturedProcess(process)
        await cap.write_stdin("ignored")
        await cap.result()
        return cap

    assert asyncio.run(go()).stdout == "hi\n"


def test_missing_streams_are_tolerated():
    async def go() -> CapturedProcess:
        process = await asyncio.create_subprocess_shell("exit 0")
        cap = CapturedProcess(process)
        await cap.result()
        return cap

    cap = asyncio.run(go())
    assert (cap.stdout, cap.stderr, cap.output, cap.exit_code) == ("", "", "", 0)


def test_a_none_return_code_counts_as_one():
    class FakeProcess:
        stdout = None
        stderr = None
        stdin = None

        async def wait(self) -> None:
            return None

    async def go() -> CapturedProcess:
        cap = CapturedProcess(FakeProcess())  # type: ignore[arg-type]
        await cap.result()
        return cap

    cap = asyncio.run(go())
    assert cap.exit_code == 1
    assert cap.status == "completed"
