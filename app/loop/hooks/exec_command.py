"""
Running one command hook.

Ported from `reference/claude-code/src/utils/hooks.ts` -> `execCommandHook` (and the
`executeInBackground` call it makes).

* The command runs through the shell (`/bin/sh -c`, Node's `spawn(..., {shell: true})`), or
  through `pwsh -NoProfile -NonInteractive -Command` when `shell: "powershell"`.
* The hook input is one JSON line on stdin.
* Environment: the parent's, plus `TDDAGENTS_PROJECT_DIR` (upstream's `CLAUDE_PROJECT_DIR`,
  renamed) pointing at the project root.
* Working directory: the run's working directory (upstream's `getCwd()`, which for this
  loop is the workspace), falling back to the project root when it does not exist on this
  host — a remote sandbox's root, for instance (upstream falls back to `getOriginalCwd()`).
* Timeout: the hook's own `timeout` seconds, else ten minutes. A timed-out or aborted hook is
  killed and reported `aborted`, which the caller turns into `hook_cancelled`.
* Async: `async`/`asyncRewake` in the config, or a first stdout line that parses as
  `{"async": true}`, backgrounds the process (see `async_registry.py`) and returns at once.

Not ported: Windows path conversion and the `.sh` auto-prefix (this build is POSIX-only), the
plugin variables (`CLAUDE_PLUGIN_ROOT`, plugin options — there are no plugins),
`CLAUDE_ENV_FILE` (it feeds the persistent shell environment upstream's Bash tool sources,
which this Bash tool does not have), `CLAUDE_CODE_SHELL_PREFIX`, and the interactive
prompt-request protocol (a headless run has no one to answer it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.loop.hooks.async_registry import AsyncHookRegistry
from app.loop.hooks.events import TOOL_HOOK_EXECUTION_TIMEOUT_MS
from app.loop.hooks.output import is_async_hook_json_output
from app.loop.hooks.process import ABORT_POLL_SECONDS, CapturedProcess, is_aborted
from app.loop.hooks.schemas import DEFAULT_HOOK_SHELL, CommandHook

PROJECT_DIR_ENV = "TDDAGENTS_PROJECT_DIR"


@dataclass(frozen=True, slots=True)
class CommandHookResult:
    """`execCommandHook`'s result."""

    stdout: str
    stderr: str
    output: str
    status: int
    aborted: bool = False
    backgrounded: bool = False


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0]


def hook_env(project_dir: str) -> dict[str, str]:
    """The child's environment: the parent's plus the project directory."""
    env = dict(os.environ)
    env[PROJECT_DIR_ENV] = project_dir
    return env


def safe_cwd(cwd: str, project_dir: str) -> str:
    """The working directory, or the project root when the working directory is missing."""
    return cwd if Path(cwd).is_dir() else project_dir


async def _spawn(hook: CommandHook, cwd: str, env: dict[str, str]) -> asyncio.subprocess.Process:
    shell_type = hook.shell or DEFAULT_HOOK_SHELL
    pipes: dict[str, Any] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": cwd,
        "env": env,
        "start_new_session": True,
    }
    if shell_type == "powershell":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            raise RuntimeError(
                f"Hook \"{hook.command}\" has shell: 'powershell' but no PowerShell executable "
                "(pwsh or powershell) was found on PATH. Install PowerShell, or remove "
                "\"shell\": \"powershell\" to use bash."
            )
        return await asyncio.create_subprocess_exec(
            pwsh, "-NoProfile", "-NonInteractive", "-Command", hook.command, **pipes
        )
    return await asyncio.create_subprocess_shell(hook.command, **pipes)


async def exec_command_hook(
    hook: CommandHook,
    hook_event: str,
    hook_name: str,
    json_input: str,
    signal: Any,
    *,
    cwd: str,
    project_dir: str,
    registry: AsyncHookRegistry | None = None,
    hook_id: str | None = None,
    timeout_ms: int | None = None,
    force_sync: bool = False,
) -> CommandHookResult:
    """
    `execCommandHook`. Failures to start or to talk to the child become a status-1 result
    with the error in stderr; they never raise, so one broken hook cannot fail its siblings.
    """
    hook_id = hook_id or str(uuid.uuid4())
    if timeout_ms is None:
        timeout_ms = int(hook.timeout * 1000) if hook.timeout else TOOL_HOOK_EXECUTION_TIMEOUT_MS
    try:
        process = await _spawn(hook, safe_cwd(cwd, project_dir), hook_env(project_dir))
    except Exception as exc:  # noqa: BLE001 - upstream reports any spawn error as status 1
        message = f"Error occurred while executing hook command: {exc}"
        return CommandHookResult(stdout="", stderr=message, output=message, status=1)
    capture = CapturedProcess(process)
    process_id = f"async_hook_{process.pid}"

    if (hook.run_async or hook.async_rewake) and not force_sync and registry is not None:
        await capture.write_stdin(json_input + "\n")
        if hook.async_rewake:
            registry.register_rewake(hook_name=hook_name, capture=capture)
        else:
            registry.register_pending(
                process_id=process_id,
                hook_id=hook_id,
                async_response={"async": True, "asyncTimeout": timeout_ms},
                hook_name=hook_name,
                hook_event=hook_event,
                command=hook.command,
                capture=capture,
            )
        return CommandHookResult(stdout="", stderr="", output="", status=0, backgrounded=True)

    try:
        await capture.write_stdin(json_input + "\n")
    except (BrokenPipeError, ConnectionResetError):
        message = "Hook command closed stdin before hook input was fully written (EPIPE)"
        capture.kill()
        await capture.result()
        return CommandHookResult(stdout="", stderr=message, output=message, status=1)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    initial_checked = False
    while True:
        if capture.done.done():
            break
        if is_aborted(signal) or loop.time() >= deadline:
            capture.kill()
            await capture.result()
            return CommandHookResult(
                stdout=capture.stdout,
                stderr=capture.stderr,
                output=capture.output,
                status=capture.exit_code if capture.exit_code is not None else 1,
                aborted=True,
            )
        if not initial_checked:
            first = _first_line(capture.stdout).strip()
            if "}" in first:
                initial_checked = True
                if _is_async_line(first) and not force_sync and registry is not None:
                    registry.register_pending(
                        process_id=process_id,
                        hook_id=hook_id,
                        async_response=json.loads(first),
                        hook_name=hook_name,
                        hook_event=hook_event,
                        command=hook.command,
                        capture=capture,
                    )
                    return CommandHookResult(
                        stdout=capture.stdout, stderr=capture.stderr, output=capture.output, status=0
                    )
        capture.stdout_changed.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(_either(capture)), timeout=min(ABORT_POLL_SECONDS, max(deadline - loop.time(), 0))
            )

    code = await capture.result()
    return CommandHookResult(stdout=capture.stdout, stderr=capture.stderr, output=capture.output, status=code)


async def _either(capture: CapturedProcess) -> None:
    """Wake on new stdout or on exit, whichever comes first."""
    changed: asyncio.Future[Any] = asyncio.ensure_future(capture.stdout_changed.wait())
    done: asyncio.Future[Any] = capture.done
    try:
        await asyncio.wait({changed, done}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        changed.cancel()


def _is_async_line(line: str) -> bool:
    try:
        parsed = json.loads(line)
    except ValueError:
        return False
    return isinstance(parsed, dict) and is_async_hook_json_output(parsed)


__all__ = [
    "PROJECT_DIR_ENV",
    "CommandHookResult",
    "exec_command_hook",
    "hook_env",
    "safe_cwd",
]
