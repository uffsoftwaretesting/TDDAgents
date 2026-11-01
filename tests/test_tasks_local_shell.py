"""
Background shells: `LocalShellTask` and the Bash tool's background paths (ports of
`LocalShellTask.tsx`, `killShellTasks.ts`, `BashTool.runShellCommand`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import Any

import app.tasks.local_shell_task.local_shell_task as lst
import app.tools.bash_tool.bash_tool as bash_mod
from app.state.app_state_store import AppStateStore
from app.tasks.local_shell_task.guards import LocalShellTaskState, is_local_shell_task
from app.tasks.local_shell_task.kill_shell_tasks import (
    kill_all_shell_tasks,
    kill_shell_tasks_for_agent,
    kill_task,
)
from app.tool import tool_context_for
from app.tools.bash_tool.bash_tool import build_bash_tool
from app.utils.message_queue_manager import CommandQueue, QueuedCommand
from app.workspace.base import CommandResult
from tests.conftest import FakeWorkspace


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _ctx(ws: Any = None, **kw: Any) -> Any:
    ws = ws if ws is not None else FakeWorkspace()
    return tool_context_for(AppStateStore(), workspace=ws, run_id="r1", command_queue=CommandQueue(), **kw)


def _held(ws: FakeWorkspace, command: str, output: str = "", code: int = 0) -> asyncio.Event:
    hold = asyncio.Event()
    ws.spawn_hold[command] = hold
    ws.command_results[command] = CommandResult(stdout=output, stderr="", exit_code=code, duration=0.0,
                                                workspace="sandbox")
    return hold


def _task_id(content: str) -> str:
    return content.split("ID: ")[1].split(".")[0]


def test_looks_like_prompt():
    for tail in ("Continue? (y/n)", "Overwrite [y/N]", "Proceed (yes/no)", "Do you want to go on?",
                 "Press Enter to continue", "Continue?", "Overwrite?", "x\nAre you sure? "):
        assert lst.looks_like_prompt(tail), tail
    assert not lst.looks_like_prompt("building...\n")
    assert not lst.looks_like_prompt("Continue? (y/n)\nmore output")


def test_is_local_shell_task():
    assert is_local_shell_task(LocalShellTaskState(id="b", type="local_bash", status="running", description="",
                                                   output_file="", start_time=0.0))
    assert not is_local_shell_task(object())


def test_run_in_background_registers_a_task_and_notifies_once_when_it_ends():
    async def go() -> Any:
        ws = FakeWorkspace()
        hold = _held(ws, "make serve", "serving\n")
        ctx = dataclasses.replace(_ctx(ws), tool_use_id="tu1")
        res = await build_bash_tool().call({"command": "make serve", "description": "Serve",
                                            "run_in_background": True}, ctx)
        task_id = _task_id(res.content)
        running = ctx.get_app_state().tasks[task_id]
        hold.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if ctx.get_app_state().tasks[task_id].status != "running":
                break
        return ctx, res, task_id, running

    ctx, res, task_id, running = run(go())
    output = Path(running.output_file)
    assert res.content == f"Command running in background with ID: {task_id}. Output is being written to: {output}"
    assert (running.status, running.is_backgrounded, running.command, running.description) == (
        "running", True, "make serve", "Serve")
    done = ctx.get_app_state().tasks[task_id]
    assert (done.status, done.result, done.shell_command, done.notified) == ("completed", (0, False), None, True)
    assert output.read_text() == "serving\n"
    (note,) = ctx.command_queue.snapshot()
    assert note.mode == "task-notification" and note.priority == "later"
    assert note.value == (
        f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>tu1</tool-use-id>\n"
        f"<output-file>{output}</output-file>\n<status>completed</status>\n"
        '<summary>Background command "Serve" completed (exit code 0)</summary>\n</task-notification>')


def test_a_failed_background_command_says_so():
    async def go() -> Any:
        ws = FakeWorkspace()
        _held(ws, "false", code=2).set()
        ctx = _ctx(ws)
        await build_bash_tool().call({"command": "false", "run_in_background": True}, ctx)
        await asyncio.sleep(0.05)
        return ctx

    ctx = run(go())
    (task,) = ctx.get_app_state().tasks.values()
    assert (task.status, task.result) == ("failed", (2, False))
    summary = '<summary>Background command "false" failed with exit code 2</summary>'
    assert summary in ctx.command_queue.snapshot()[0].value


def test_a_stopped_background_command_is_killed_without_a_notification():
    async def go() -> Any:
        ws = FakeWorkspace()
        _held(ws, "server")
        ctx = _ctx(ws)
        res = await build_bash_tool().call({"command": "server", "run_in_background": True}, ctx)
        kill_task(_task_id(res.content), ctx.set_app_state)
        await asyncio.sleep(0.05)
        return ctx, _task_id(res.content)

    ctx, task_id = run(go())
    task = ctx.get_app_state().tasks[task_id]
    assert (task.status, task.notified, task.shell_command) == ("killed", True, None)
    assert ctx.command_queue.snapshot() == ()
    kill_task(task_id, ctx.set_app_state)  # not running any more: unchanged
    assert ctx.get_app_state().tasks[task_id] is task


def test_a_notification_is_enqueued_at_most_once_and_says_stopped_for_a_killed_task():
    async def go() -> Any:
        ctx = _ctx()
        ctx.set_app_state(lambda s: dataclasses.replace(s, tasks={"b1": LocalShellTaskState(
            id="b1", type="local_bash", status="killed", description="d", output_file="/o", start_time=0.0)}))
        lst.enqueue_shell_notification(ctx, "b1", "d", "killed", None, "/o")
        lst.enqueue_shell_notification(ctx, "b1", "d", "killed", None, "/o")
        lst.enqueue_shell_notification(ctx, "missing", "d", "completed", 0, "/o")
        return ctx

    ctx = run(go())
    (note,) = ctx.command_queue.snapshot()
    assert '<summary>Background command "d" was stopped</summary>' in note.value
    assert "<tool-use-id>" not in note.value


def test_a_summary_without_an_exit_code_and_without_a_queue():
    ctx = _ctx()
    ctx.set_app_state(lambda s: dataclasses.replace(s, tasks={"b1": LocalShellTaskState(
        id="b1", type="local_bash", status="running", description="a<b", output_file="/o", start_time=0.0)}))
    lst.enqueue_shell_notification(ctx, "b1", "a<b", "completed", None, "/o")
    assert '<summary>Background command "a&lt;b" completed</summary>' in ctx.command_queue.snapshot()[0].value
    quiet = dataclasses.replace(ctx, command_queue=None)
    quiet.set_app_state(lambda s: dataclasses.replace(s, tasks={"b2": LocalShellTaskState(
        id="b2", type="local_bash", status="running", description="x", output_file="/o", start_time=0.0)}))
    lst.enqueue_shell_notification(quiet, "b2", "x", "failed", None, "/o")
    assert quiet.get_app_state().tasks["b2"].notified is True


def test_a_slow_command_is_registered_in_the_foreground_and_dropped_when_it_finishes(monkeypatch):
    monkeypatch.setattr(bash_mod, "PROGRESS_THRESHOLD_MS", 0)

    async def go() -> Any:
        ws = FakeWorkspace()
        hold = _held(ws, "build", "built\n")
        ctx = _ctx(ws)
        call = asyncio.ensure_future(build_bash_tool().call({"command": "build"}, ctx))
        await asyncio.sleep(0.1)
        registered = dict(ctx.get_app_state().tasks)
        hold.set()
        return ctx, registered, await call

    ctx, registered, res = run(go())
    (task,) = registered.values()
    assert (task.is_backgrounded, task.status) == (False, "running")
    assert res.content == "built" and ctx.get_app_state().tasks == {}


def test_a_timeout_backgrounds_a_registered_command_in_place(monkeypatch):
    monkeypatch.setattr(bash_mod, "PROGRESS_THRESHOLD_MS", 0)

    async def go() -> Any:
        ws = FakeWorkspace()
        hold = _held(ws, "python long.py", "half\n")
        ctx = _ctx(ws)
        res = await build_bash_tool().call({"command": "python long.py", "timeout": 200}, ctx)
        task_id = _task_id(res.content)
        backgrounded = ctx.get_app_state().tasks[task_id]
        hold.set()
        await asyncio.sleep(0.05)
        return ctx, task_id, backgrounded

    ctx, task_id, backgrounded = run(go())
    assert backgrounded.is_backgrounded is True and backgrounded.status == "running"
    assert ctx.get_app_state().tasks[task_id].status == "completed"
    assert len(ctx.command_queue.snapshot()) == 1


def test_a_timeout_before_registration_spawns_the_background_task():
    async def go() -> Any:
        ws = FakeWorkspace()
        hold = _held(ws, "python long.py")
        ctx = _ctx(ws)
        res = await build_bash_tool().call({"command": "python long.py", "timeout": 100}, ctx)
        hold.set()
        await asyncio.sleep(0.05)
        return ctx, res

    ctx, res = run(go())
    task = ctx.get_app_state().tasks[_task_id(res.content)]
    assert task.is_backgrounded is True and task.status == "completed"


def test_a_sleep_is_killed_on_timeout_and_disabled_background_ignores_the_flag(monkeypatch):
    async def go(command: str, **args: Any) -> Any:
        ws = FakeWorkspace()
        _held(ws, command)
        return await build_bash_tool().call({"command": command, **args}, _ctx(ws))

    killed = run(go("sleep 30", timeout=50))
    assert (killed.content, killed.is_error) == ("Exit code 143\nCommand timed out after 0s", True)
    monkeypatch.setenv("TDDAGENTS_DISABLE_BACKGROUND_TASKS", "1")
    foreground = run(go("python long.py", timeout=50, run_in_background=True))
    assert foreground.content.startswith("Exit code 143")


def test_a_cancelled_run_kills_the_foreground_command():
    async def go() -> Any:
        ws = FakeWorkspace()
        _held(ws, "hang")
        ctx = _ctx(ws)
        call = asyncio.ensure_future(build_bash_tool().call({"command": "hang"}, ctx))
        await asyncio.sleep(0.05)
        ctx.cancel.cancel()
        return await call

    res = run(go())
    assert res.is_error is True
    assert res.content == "<error>Command was aborted before completion</error>"


def test_leaving_the_project_resets_the_shell_and_says_so(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    ws = FakeWorkspace()
    ws.root = str(root)  # type: ignore[attr-defined]
    ws.spawn_cwd["cd /"] = "/"
    ctx = _ctx(ws)
    res = run(build_bash_tool().call({"command": "cd /"}, ctx))
    assert res.content == f"Shell cwd was reset to {root}"
    from app.utils.cwd import get_cwd

    assert get_cwd(ctx) == str(root)


def test_a_large_output_is_persisted_with_a_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "10")
    ws = FakeWorkspace()
    ws.root = str(tmp_path)  # type: ignore[attr-defined]
    ws.command_results["big"] = CommandResult(stdout="line\n" * 100, stderr="", exit_code=0, duration=0.0,
                                              workspace="sandbox")
    res = run(build_bash_tool().call({"command": "big"}, _ctx(ws)))
    assert res.content.startswith("<persisted-output>\nOutput too large (500 bytes). Full output saved to: ")
    saved = res.content.split("Full output saved to: ")[1].split("\n")[0]
    assert Path(saved).read_text() == "line\n" * 100


def test_kill_shell_tasks_for_an_agent_and_for_the_session():
    async def go() -> Any:
        ws = FakeWorkspace()
        for command in ("a", "b", "c"):
            _held(ws, command)
        ctx = _ctx(ws)
        worker = dataclasses.replace(ctx, agent_id="w1")
        mine = _task_id((await build_bash_tool().call({"command": "a", "run_in_background": True}, worker)).content)
        other = _task_id((await build_bash_tool().call({"command": "b", "run_in_background": True}, ctx)).content)
        ctx.command_queue.enqueue_pending_notification(QueuedCommand(value="for w1", mode="task-notification",
                                                                     agent_id="w1"))
        kill_shell_tasks_for_agent("w1", ctx.get_app_state, ctx.set_app_state, ctx.command_queue)
        after_agent = {k: v.status for k, v in ctx.get_app_state().tasks.items()}
        third = _task_id((await build_bash_tool().call({"command": "c", "run_in_background": True}, ctx)).content)
        kill_all_shell_tasks(ctx.get_app_state, ctx.set_app_state)
        await asyncio.sleep(0.05)
        return ctx, mine, other, third, after_agent

    ctx, mine, other, third, after_agent = run(go())
    assert after_agent == {mine: "killed", other: "running"}
    assert {k: v.status for k, v in ctx.get_app_state().tasks.items()} == {
        mine: "killed", other: "killed", third: "killed"}
    assert ctx.command_queue.snapshot() == ()
    kill_shell_tasks_for_agent("w1", ctx.get_app_state, ctx.set_app_state)  # no queue: fine


def test_mark_and_unregister():
    ctx = _ctx()
    fg = LocalShellTaskState(id="b1", type="local_bash", status="running", description="", output_file="",
                             start_time=0.0, is_backgrounded=False)
    bg = dataclasses.replace(fg, id="b2", is_backgrounded=True)
    ctx.set_app_state(lambda s: dataclasses.replace(s, tasks={"b1": fg, "b2": bg}))
    lst.mark_task_notified("b1", ctx.set_app_state)
    lst.mark_task_notified("b1", ctx.set_app_state)
    assert ctx.get_app_state().tasks["b1"].notified is True
    lst.unregister_foreground("b2", ctx.set_app_state)
    lst.unregister_foreground("missing", ctx.set_app_state)
    lst.unregister_foreground("b1", ctx.set_app_state)
    assert list(ctx.get_app_state().tasks) == ["b2"]
    assert lst.background_existing_foreground_task(ctx, "missing", None, "d") is False
    assert lst.background_existing_foreground_task(ctx, "b2", None, "d") is False


def test_the_stall_watchdog_reports_a_prompt_once(tmp_path, monkeypatch):
    monkeypatch.setattr(lst, "STALL_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(lst, "STALL_THRESHOLD_SECONDS", 0.02)
    output = tmp_path / "b1.output"

    async def go() -> Any:
        ctx = _ctx()
        output.write_text("Overwrite file? (y/n)")
        watchdog = asyncio.ensure_future(lst._stall_watchdog(ctx, "b1", "cp", str(output), "tu", "w1"))
        await asyncio.wait_for(watchdog, 2)
        return ctx

    ctx = run(go())
    (note,) = ctx.command_queue.snapshot()
    assert (note.priority, note.agent_id) == ("next", "w1")
    assert note.value == (
        "<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>tu</tool-use-id>\n"
        f"<output-file>{output}</output-file>\n"
        '<summary>Background command "cp" appears to be waiting for interactive input</summary>\n'
        "</task-notification>\nLast output:\nOverwrite file? (y/n)\n\n"
        "The command is likely blocked on an interactive prompt. Kill this task and re-run with piped input "
        "(e.g., `echo y | command`) or a non-interactive flag if one exists.")


def test_the_stall_watchdog_stays_quiet_on_slow_output(tmp_path, monkeypatch):
    monkeypatch.setattr(lst, "STALL_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(lst, "STALL_THRESHOLD_SECONDS", 0.02)
    output = tmp_path / "b1.output"

    async def go() -> Any:
        ctx = _ctx()
        watchdog = asyncio.ensure_future(lst._stall_watchdog(ctx, "b1", "build", str(output), None, None))
        await asyncio.sleep(0.03)  # no file yet
        output.write_text("compiling\n")
        await asyncio.sleep(0.08)
        watchdog.cancel()
        return ctx

    assert run(go()).command_queue.snapshot() == ()
    assert os.path.exists(output)
