"""Tests for `app/loop/hooks/exec_command.py` (`execCommandHook`), against real shells."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.loop.context import CancelToken
from app.loop.hooks import exec_command as ec
from app.loop.hooks.async_registry import AsyncHookRegistry
from app.loop.hooks.exec_command import (
    PROJECT_DIR_ENV,
    CommandHookResult,
    exec_command_hook,
    hook_env,
    safe_cwd,
)
from app.loop.hooks.process import CapturedProcess
from app.loop.hooks.schemas import CommandHook


def script(tmp_path: Path, name: str, body: str) -> str:
    """A hook body in a file, so the command text and its output never share text."""
    path = tmp_path / name
    path.write_text(body)
    return f"sh {path}"


def run(hook: CommandHook, tmp_path: Path, json_input: str = "{}", signal: Any = None, **kwargs: Any) -> Any:
    kwargs.setdefault("cwd", str(tmp_path))
    kwargs.setdefault("project_dir", str(tmp_path))
    return asyncio.run(exec_command_hook(hook, "PreToolUse", "PreToolUse:Bash", json_input, signal, **kwargs))


def test_constants_and_helpers(tmp_path, monkeypatch):
    assert PROJECT_DIR_ENV == "TDDAGENTS_PROJECT_DIR"
    monkeypatch.setenv("SOME_PARENT_VAR", "kept")
    env = hook_env("/proj")
    assert env["TDDAGENTS_PROJECT_DIR"] == "/proj"
    assert env["SOME_PARENT_VAR"] == "kept"
    assert "TDDAGENTS_PROJECT_DIR" not in os.environ
    assert safe_cwd(str(tmp_path), "/fallback") == str(tmp_path)
    assert safe_cwd(str(tmp_path / "missing"), "/fallback") == "/fallback"
    (tmp_path / "file").write_text("")
    assert safe_cwd(str(tmp_path / "file"), "/fallback") == "/fallback"


def test_stdin_is_one_json_line_and_streams_are_captured(tmp_path):
    hook = CommandHook(command=script(tmp_path, "h.sh", "cat; printf warn >&2; exit 3"))
    result = run(hook, tmp_path, json_input='{"tool_name": "Bash"}')
    assert result == CommandHookResult(
        stdout='{"tool_name": "Bash"}\n', stderr="warn", output=result.output, status=3
    )
    assert sorted(result.output) == sorted('{"tool_name": "Bash"}\nwarn')
    assert result.aborted is False and result.backgrounded is False


def test_env_and_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    hook = CommandHook(command=script(tmp_path, "h.sh", 'pwd; echo "$TDDAGENTS_PROJECT_DIR"'))
    result = run(hook, tmp_path, cwd=str(work), project_dir=str(tmp_path / "proj"))
    assert result.stdout == f"{work}\n{tmp_path / 'proj'}\n"
    assert result.status == 0


def test_missing_cwd_falls_back_to_the_project_dir(tmp_path):
    hook = CommandHook(command=script(tmp_path, "h.sh", "pwd"))
    result = run(hook, tmp_path, cwd=str(tmp_path / "gone"), project_dir=str(tmp_path))
    assert result.stdout == f"{tmp_path}\n"


def test_a_missing_binary_is_status_127_from_the_shell(tmp_path):
    result = run(CommandHook(command="definitely_not_a_binary_xyz"), tmp_path)
    assert result.status == 127
    assert "not found" in result.stderr


def test_a_spawn_failure_is_a_status_one_result(tmp_path):
    result = run(CommandHook(command="echo x"), tmp_path, cwd=str(tmp_path / "gone"),
                 project_dir=str(tmp_path / "also-gone"))
    assert result.status == 1
    assert result.stdout == ""
    assert result.stderr.startswith("Error occurred while executing hook command: ")
    assert result.output == result.stderr


def test_powershell_without_a_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = run(CommandHook(command="Get-Date", shell="powershell"), tmp_path)
    assert result.status == 1
    assert result.stderr == (
        "Error occurred while executing hook command: Hook \"Get-Date\" has shell: 'powershell' but no "
        "PowerShell executable (pwsh or powershell) was found on PATH. Install PowerShell, or remove "
        "\"shell\": \"powershell\" to use bash."
    )


def test_powershell_is_spawned_with_upstreams_flags(tmp_path, monkeypatch):
    fake = tmp_path / "pwsh"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do echo "[$a]"; done\n')
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "pwsh" else None)
    result = run(CommandHook(command="Write-Output hi", shell="powershell"), tmp_path)
    assert result.stdout == "[-NoProfile]\n[-NonInteractive]\n[-Command]\n[Write-Output hi]\n"


def test_powershell_falls_back_to_the_windows_name(tmp_path, monkeypatch):
    fake = tmp_path / "powershell"
    fake.write_text("#!/bin/sh\necho legacy\n")
    fake.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "powershell" else None)
    assert run(CommandHook(command="x", shell="powershell"), tmp_path).stdout == "legacy\n"


def test_hook_timeout_kills_and_reports_aborted(tmp_path):
    hook = CommandHook(command=script(tmp_path, "h.sh", "echo partial; sleep 5"), timeout=0.3)
    started = time.monotonic()
    result = run(hook, tmp_path)
    assert time.monotonic() - started < 1.5
    assert result.aborted is True
    assert result.stdout == "partial\n"
    assert result.status < 0


def test_explicit_timeout_ms_wins_over_the_hooks(tmp_path):
    hook = CommandHook(command=script(tmp_path, "h.sh", "sleep 5"), timeout=60)
    started = time.monotonic()
    result = run(hook, tmp_path, timeout_ms=200)
    assert result.aborted is True
    assert time.monotonic() - started < 1.5


def test_the_default_timeout_is_ten_minutes(tmp_path, monkeypatch):
    from app.loop.hooks.events import TOOL_HOOK_EXECUTION_TIMEOUT_MS

    assert getattr(ec, "TOOL_HOOK_EXECUTION_TIMEOUT_MS") is TOOL_HOOK_EXECUTION_TIMEOUT_MS  # noqa: B009
    assert TOOL_HOOK_EXECUTION_TIMEOUT_MS == 600_000
    monkeypatch.setattr("app.loop.hooks.exec_command.TOOL_HOOK_EXECUTION_TIMEOUT_MS", 150)
    started = time.monotonic()
    result = run(CommandHook(command=script(tmp_path, "h.sh", "sleep 5")), tmp_path)
    assert result.aborted is True
    assert time.monotonic() - started < 1.5


def test_a_quick_hook_is_not_cut_off_by_its_timeout(tmp_path):
    result = run(CommandHook(command=script(tmp_path, "h.sh", "sleep 0.1; echo done"), timeout=5), tmp_path)
    assert (result.stdout, result.status, result.aborted) == ("done\n", 0, False)


def test_a_cancel_token_aborts_the_hook(tmp_path):
    token = CancelToken()

    async def go() -> Any:
        hook = CommandHook(command=script(tmp_path, "h.sh", "sleep 5"))
        task = asyncio.ensure_future(exec_command_hook(
            hook, "Stop", "Stop", "{}", token, cwd=str(tmp_path), project_dir=str(tmp_path)))
        await asyncio.sleep(0.1)
        token.cancel()
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(go())
    assert result.aborted is True


def test_an_already_cancelled_signal_aborts_at_once(tmp_path):
    token = CancelToken()
    token.cancel()
    result = run(CommandHook(command=script(tmp_path, "h.sh", "sleep 5")), tmp_path, signal=token)
    assert result.aborted is True


def test_a_config_async_hook_is_backgrounded_and_registered(tmp_path):
    async def go() -> tuple[Any, AsyncHookRegistry]:
        registry = AsyncHookRegistry()
        body = 'read line; echo "got $line" >&2; echo \'{"systemMessage": "later"}\''
        hook = CommandHook(command=script(tmp_path, "h.sh", body),
                           run_async=True, timeout=2)
        result = await exec_command_hook(
            hook, "PostToolUse", "PostToolUse:Bash", '{"in": 1}', None,
            cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry, hook_id="hid",
        )
        [pending] = registry.get_pending()
        assert pending.hook_id == "hid"
        assert pending.timeout == 2000
        assert (pending.hook_name, pending.hook_event, pending.command) == (
            "PostToolUse:Bash", "PostToolUse", hook.command)
        assert pending.process_id.startswith("async_hook_")
        assert pending.capture is not None
        await pending.capture.result()
        return result, registry

    result, registry = asyncio.run(go())
    assert result == CommandHookResult(stdout="", stderr="", output="", status=0, backgrounded=True)
    [response] = asyncio.run(registry.check_for_responses())
    assert response.response == {"systemMessage": "later"}
    assert response.stderr == 'got {"in": 1}\n'


def test_a_config_async_hook_without_registry_runs_in_the_foreground(tmp_path):
    hook = CommandHook(command=script(tmp_path, "h.sh", "echo fg"), run_async=True)
    result = run(hook, tmp_path)
    assert (result.stdout, result.backgrounded) == ("fg\n", False)


def test_force_sync_keeps_an_async_hook_in_the_foreground(tmp_path):
    registry = AsyncHookRegistry()
    hook = CommandHook(command=script(tmp_path, "h.sh", "echo fg"), run_async=True, async_rewake=True)
    result = run(hook, tmp_path, registry=registry, force_sync=True)
    assert (result.stdout, result.backgrounded) == ("fg\n", False)
    assert registry.get_pending() == []


def test_an_async_rewake_hook_is_watched_not_registered(tmp_path):
    async def go() -> list[str]:
        registry = AsyncHookRegistry()
        hook = CommandHook(command=script(tmp_path, "h.sh", "echo boom >&2; exit 2"), async_rewake=True)
        result = await exec_command_hook(
            hook, "Stop", "Stop", "{}", None, cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry)
        assert result.backgrounded is True
        assert registry.get_pending() == []
        for _ in range(100):
            await asyncio.sleep(0.01)
            if registry._notifications:
                break
        return registry.drain_notifications()

    assert asyncio.run(go()) == [
        '<system-reminder>\nStop hook blocking error from command "Stop": boom\n\n</system-reminder>'
    ]


def test_an_async_first_line_backgrounds_the_hook(tmp_path):
    async def go() -> tuple[Any, AsyncHookRegistry]:
        registry = AsyncHookRegistry()
        body = 'echo \'{"async": true, "asyncTimeout": 1234}\'; sleep 0.2; echo \'{"systemMessage": "x"}\''
        hook = CommandHook(command=script(tmp_path, "h.sh", body))
        started = time.monotonic()
        result = await exec_command_hook(
            hook, "PostToolUse", "PostToolUse:Bash", "{}", None,
            cwd=str(tmp_path), project_dir=str(tmp_path), registry=registry, hook_id="h1",
        )
        assert time.monotonic() - started < 0.2
        [pending] = registry.get_pending()
        assert (pending.timeout, pending.hook_id) == (1234, "h1")
        assert pending.capture is not None
        await pending.capture.result()
        return result, registry

    result, registry = asyncio.run(go())
    assert result.status == 0
    assert result.stdout == '{"async": true, "asyncTimeout": 1234}\n'
    assert result.backgrounded is False
    [response] = asyncio.run(registry.check_for_responses())
    assert response.response == {"systemMessage": "x"}


def test_an_async_first_line_without_registry_waits_for_the_hook(tmp_path):
    body = 'echo \'{"async": true}\'; sleep 0.1; echo end'
    result = run(CommandHook(command=script(tmp_path, "h.sh", body)), tmp_path)
    assert result.stdout == '{"async": true}\nend\n'


def test_an_async_first_line_under_force_sync_waits(tmp_path):
    registry = AsyncHookRegistry()
    body = 'echo \'{"async": true}\'; sleep 0.1; echo end'
    result = run(CommandHook(command=script(tmp_path, "h.sh", body)), tmp_path, registry=registry, force_sync=True)
    assert result.stdout == '{"async": true}\nend\n'
    assert registry.get_pending() == []


@pytest.mark.parametrize("first", ['{"decision": "approve"}', '{"async": false}', "{not json}", '["}"]'])
def test_a_first_line_that_is_not_an_async_response_runs_to_completion(tmp_path, first):
    registry = AsyncHookRegistry()
    body = f"echo '{first}'; sleep 0.1; echo end"
    result = run(CommandHook(command=script(tmp_path, "h.sh", body)), tmp_path, registry=registry)
    assert result.stdout == f"{first}\nend\n"
    assert registry.get_pending() == []


def test_a_hook_that_ignores_stdin_still_succeeds(tmp_path):
    body = "exec 0<&-; echo done"
    big = json.dumps({"x": "y" * 200_000})
    result = run(CommandHook(command=script(tmp_path, "h.sh", body)), tmp_path, json_input=big)
    assert result.stdout == "done\n"
    assert result.status == 0


def test_a_broken_stdin_pipe_is_reported_as_epipe(tmp_path, monkeypatch):
    async def broken(self: Any, data: str) -> None:
        raise BrokenPipeError()

    monkeypatch.setattr(CapturedProcess, "write_stdin", broken)
    result = run(CommandHook(command=script(tmp_path, "h.sh", "sleep 5")), tmp_path)
    message = "Hook command closed stdin before hook input was fully written (EPIPE)"
    assert result == CommandHookResult(stdout="", stderr=message, output=message, status=1)
