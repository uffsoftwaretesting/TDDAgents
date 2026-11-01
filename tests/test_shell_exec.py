"""
The Bash tool's process layer: `ShellCommand`, `exec_shell`, the output file and cwd tracking
(ports of `ShellCommand.ts`, `Shell.ts`, `TaskOutput.ts`, `bootstrap/state.ts`, `utils/cwd.ts`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.bootstrap.state import get_cwd_state, get_original_cwd, set_cwd
from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.utils.cwd import get_cwd, get_original_cwd_for
from app.utils.format import format_duration
from app.utils.shell.shell import cwd_file_path, exec_shell
from app.utils.shell_command import (
    ABORTED_EXIT_CODE,
    SIGKILL_EXIT_CODE,
    SIGTERM_EXIT_CODE,
    ShellCommand,
    create_aborted_command,
    create_failed_command,
    prepend_stderr,
)
from app.utils.task.task_output import TaskOutput
from app.workspace.base import CommandResult, WorkspaceProviderError
from tests.conftest import FakeShellProcess, FakeWorkspace


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _output(tmp_path: Path, text: str = "") -> TaskOutput:
    path = tmp_path / "b1.output"
    path.write_text(text, encoding="utf-8")
    return TaskOutput("b1", path)


def test_format_duration():
    assert [format_duration(ms) for ms in (0, 0.5, 999, 42_000, 59_999)] == ["0s", "0.0s", "0s", "42s", "59s"]
    assert format_duration(120_000) == "2m 0s"
    assert format_duration(119_600) == "2m 0s"  # 59.6s rounds up into the next minute
    assert format_duration(3_599_600) == "1h 0m 0s"
    assert format_duration(3_723_000) == "1h 2m 3s"
    assert format_duration(86_399_600) == "1d 0h 0m"
    assert format_duration(93_780_000) == "1d 2h 3m"


def test_prepend_stderr():
    assert prepend_stderr("Timed out", "") == "Timed out"
    assert prepend_stderr("Timed out", "boom") == "Timed out boom"


def test_task_output_reads_the_head_and_knows_whether_it_was_all(tmp_path, monkeypatch):
    out = _output(tmp_path, "hello")
    assert (out.get_stdout(), out.get_stderr(), out.output_file_redundant, out.output_file_size) == (
        "hello", "", True, 5)
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "3")
    assert (out.get_stdout(), out.output_file_redundant, out.output_file_size) == ("hel", False, 5)
    out.delete_output_file()
    assert not out.path.exists()
    out.delete_output_file()  # already gone: no error
    assert out.get_stdout().startswith("<bash output unavailable: ")


def test_a_command_that_exits_gives_its_output_and_deletes_a_small_file(tmp_path):
    async def go() -> Any:
        shell = ShellCommand(FakeShellProcess(0), _output(tmp_path, "done\n"), 10_000)
        result = await shell.result
        return shell, result

    shell, result = run(go())
    assert (result.code, result.stdout, result.stderr, result.interrupted) == (0, "done\n", "", False)
    assert result.output_file_path is None and shell.status == "completed"
    assert not (tmp_path / "b1.output").exists()


def test_a_large_output_names_its_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "2")

    async def go() -> Any:
        return await ShellCommand(FakeShellProcess(0), _output(tmp_path, "abcdef"), 10_000).result

    result = run(go())
    assert (result.output_file_path, result.output_file_size, result.output_task_id) == (
        str(tmp_path / "b1.output"), 6, "b1")


def test_a_timeout_kills_with_143_and_says_so(tmp_path):
    async def go() -> Any:
        process = FakeShellProcess(0, asyncio.Event())
        shell = ShellCommand(process, _output(tmp_path, "partial"), 50)
        result = await shell.result
        return process, shell, result

    process, shell, result = run(go())
    assert process.killed and shell.status == "killed"
    assert (result.code, result.stderr, result.interrupted) == (SIGTERM_EXIT_CODE, "Command timed out after 0s", False)


def test_a_timeout_with_auto_background_hands_over_instead(tmp_path):
    async def go() -> Any:
        process = FakeShellProcess(0, asyncio.Event())
        shell = ShellCommand(process, _output(tmp_path), 50, should_auto_background=True)
        handed: list[Any] = []
        shell.on_timeout(lambda background: handed.append(background("b1")))
        await asyncio.sleep(0.1)
        status = shell.status
        process.hold.set()  # type: ignore[union-attr]
        result = await shell.result
        return process, status, handed, result

    process, status, handed, result = run(go())
    assert not process.killed and status == "backgrounded" and handed == [True]
    assert (result.code, result.background_task_id, result.output_file_path) == (0, "b1", None)
    assert (Path(tmp_path) / "b1.output").exists()  # a background task's file is kept


def test_without_a_timeout_callback_auto_background_still_kills(tmp_path):
    async def go() -> Any:
        process = FakeShellProcess(0, asyncio.Event())
        return await ShellCommand(process, _output(tmp_path), 50, should_auto_background=True).result

    assert run(go()).code == SIGTERM_EXIT_CODE


def test_kill_is_137_interrupted_and_background_only_while_running(tmp_path):
    async def go() -> Any:
        process = FakeShellProcess(0, asyncio.Event())
        shell = ShellCommand(process, _output(tmp_path), 10_000)
        shell.kill()
        result = await shell.result
        return shell, result

    shell, result = run(go())
    assert (result.code, result.interrupted, shell.status) == (SIGKILL_EXIT_CODE, True, "killed")
    assert shell.background("b1") is False
    shell.cleanup()


def test_the_size_watchdog_kills_a_runaway_background_command(tmp_path, monkeypatch):
    import app.utils.shell_command as sc

    monkeypatch.setattr(sc, "SIZE_WATCHDOG_INTERVAL_SECONDS", 0.01)

    async def go() -> Any:
        process = FakeShellProcess(0, asyncio.Event())
        shell = ShellCommand(process, _output(tmp_path, "x" * 20), 10_000, max_output_bytes=10)
        assert shell.background("b1") is True
        return await shell.result

    result = run(go())
    assert result.code == SIGKILL_EXIT_CODE
    assert result.stderr == "Background command killed: output file exceeded 5GB"


def test_the_watchdog_tolerates_a_missing_file(tmp_path, monkeypatch):
    import app.utils.shell_command as sc

    monkeypatch.setattr(sc, "SIZE_WATCHDOG_INTERVAL_SECONDS", 0.01)

    async def go() -> str:
        process = FakeShellProcess(0, asyncio.Event())
        out = _output(tmp_path)
        out.path.unlink()
        shell = ShellCommand(process, out, 10_000, max_output_bytes=10)
        shell.background("b1")
        await asyncio.sleep(0.05)
        status = shell.status
        process.hold.set()  # type: ignore[union-attr]
        await shell.result
        return status

    assert run(go()) == "backgrounded"


def test_commands_that_never_ran(tmp_path):
    async def go() -> Any:
        aborted, failed = create_aborted_command(_output(tmp_path)), create_failed_command("no shell", None)
        return aborted, await aborted.result, failed, await failed.result

    aborted, a, failed, f = run(go())
    assert (a.code, a.stderr, a.interrupted, aborted.status) == (
        ABORTED_EXIT_CODE, "Command aborted before execution", True, "killed")
    assert (f.code, f.stderr, f.pre_spawn_error, failed.status) == (1, "no shell", "no shell", "completed")
    for command in (aborted, failed):
        assert command.background("x") is False
        assert command.kill() is None and command.cleanup() is None and command.on_timeout(lambda b: None) is None


# ── exec_shell and cwd ───────────────────────────────────────────────────────

def _ctx(ws: Any, **kw: Any) -> Any:
    return tool_context_for(AppStateStore(), workspace=ws, run_id="r1", **kw)


def test_exec_shell_runs_from_the_tracked_cwd_and_follows_a_cd(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    ws = FakeWorkspace()
    ws.root = str(root)  # type: ignore[attr-defined]
    ws.command_results["cd sub"] = CommandResult(stdout="moved", stderr="", exit_code=0, duration=0.0,
                                                 workspace="sandbox")
    ws.spawn_cwd["cd sub"] = str(root / "sub")
    ctx = _ctx(ws)

    async def go() -> Any:
        first = await (await exec_shell(ctx, "cd sub", timeout_ms=10_000)).result
        second = await (await exec_shell(ctx, "ls", timeout_ms=10_000)).result
        return first, second

    first, second = run(go())
    assert first.stdout == "moved" and second.code == 0
    assert ws.spawn_log == [("cd sub", str(root)), ("ls", str(root / "sub"))]
    assert get_cwd(ctx) == str(root / "sub") and get_original_cwd_for(ctx) == str(root)


def test_a_cd_into_a_missing_directory_or_a_background_result_moves_nothing(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ws = FakeWorkspace()
    ws.root = str(root)  # type: ignore[attr-defined]
    ws.spawn_cwd["gone"] = str(tmp_path / "missing")
    ctx = _ctx(ws)
    run(_result_of(exec_shell(ctx, "gone", timeout_ms=10_000)))
    assert get_cwd(ctx) == str(root)
    ws.spawn_cwd["bg"] = str(tmp_path)

    async def background() -> None:
        shell = await exec_shell(ctx, "bg", timeout_ms=10_000)
        shell.background("b1")
        await shell.result

    run(background())
    assert get_cwd(ctx) == str(root)
    leftovers = [name for name in os.listdir(os.path.dirname(cwd_file_path("x"))) if name.startswith("tddagents-b")]
    assert all(not name.endswith("-cwd") or "b1" not in name for name in leftovers)


async def _result_of(starting: Any) -> Any:
    return await (await starting).result


def test_exec_shell_without_a_workspace_or_after_a_cancel_never_spawns(tmp_path):
    cancelled = _ctx(FakeWorkspace())
    cancelled.cancel.cancel()
    no_ws = run(_result_of(exec_shell(_ctx(None), "ls", timeout_ms=1000)))
    aborted = run(_result_of(exec_shell(cancelled, "ls", timeout_ms=1000)))
    assert (no_ws.code, no_ws.pre_spawn_error) == (1, "No workspace available")
    assert aborted.code == ABORTED_EXIT_CODE


def test_a_spawn_failure_is_a_failed_command():
    class Boom(FakeWorkspace):
        async def spawn(self, cmd: str, **kw: Any) -> Any:
            raise WorkspaceProviderError("no sandbox")

    result = run(_result_of(exec_shell(_ctx(Boom()), "ls", timeout_ms=1000)))
    assert (result.code, result.pre_spawn_error) == (1, "no sandbox")


def test_prevent_cwd_changes_passes_no_cwd_file(tmp_path):
    seen: list[Any] = []

    class Recording(FakeWorkspace):
        async def spawn(self, cmd: str, *, output_fd: int, cwd: Any = None, cwd_file: Any = None) -> Any:
            seen.append(cwd_file)
            return FakeShellProcess(0)

    run(_result_of(exec_shell(_ctx(Recording()), "ls", timeout_ms=1000, prevent_cwd_changes=True)))
    run(_result_of(exec_shell(_ctx(Recording()), "ls", timeout_ms=1000)))
    assert seen[0] is None and seen[1].endswith("-cwd")


def test_cwd_state_is_per_workspace_including_unweakrefable_ones(tmp_path):
    a, b = FakeWorkspace(), SimpleNamespace(root=str(tmp_path))
    a.root = "/a"  # type: ignore[attr-defined]
    assert (get_original_cwd(a), get_cwd_state(a)) == ("/a", "/a")
    set_cwd(a, "/a/x")
    set_cwd(b, str(tmp_path / "y"))
    set_cwd(None, "/ignored")
    assert (get_cwd_state(a), get_cwd_state(b)) == ("/a/x", str(tmp_path / "y"))
    assert get_cwd_state(None) == os.getcwd() and get_original_cwd(SimpleNamespace()) == os.getcwd()


@pytest.mark.parametrize("code", [0, 2])
def test_a_real_shell_returns_while_its_background_child_runs(python_workspace, code):
    """The live-run hang: `cd X && nohup … &` must return when the shell exits, not when the child does."""
    import time

    (Path(str(python_workspace.root)) / "sub").mkdir(exist_ok=True)
    ctx = _ctx(python_workspace)
    command = f"cd sub && nohup sleep 30 >/dev/null 2>&1 &\necho started; exit {code}"
    started = time.monotonic()
    result = run(_result_of(exec_shell(ctx, command, timeout_ms=20_000)))
    assert time.monotonic() - started < 10
    assert (result.code, result.stdout.strip()) == (code, "started")
    os.system("pkill -f 'sleep 30$'")
