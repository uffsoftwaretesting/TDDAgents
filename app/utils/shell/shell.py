"""
Running one Bash command: the output file, the spawn, and the working directory it leaves.

Ported from `reference/claude-code/src/utils/Shell.ts` -> `exec`: a `local_bash` task id, the
task's output file opened for append (stdout and stderr both go there), the command spawned
through the workspace from the tracked working directory, the parent's copy of the file
descriptor closed, and a `ShellCommand` whose result arrives when the shell exits. A
foreground command that finishes leaves its final directory (`pwd -P`) in a temp file, which
becomes the tracked cwd (`setCwd`) when it is still a directory; the file is then removed.
`should_use_sandbox` (the caller's `shouldUseSandbox`) runs the command in the run's sandbox: the
workspace wraps it (`LocalWorkspace._argv`) and cleans up after it, and the cwd file lives in the
sandbox's writable temp directory (`cwd-<id>`, `bashProvider.ts`).
Not ported: PowerShell, the shell snapshot (the login shell stands in, unsandboxed) and
`CLAUDE_CODE_SHELL_PREFIX`.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from app.bootstrap.state import set_cwd
from app.task import generate_task_id
from app.utils.cwd import get_cwd
from app.utils.shell_command import (
    ExecResult,
    FinishedShellCommand,
    ShellCommand,
    create_aborted_command,
    create_failed_command,
)
from app.utils.task.disk_output import get_task_output_path, open_task_output_for_append
from app.utils.task.task_output import TaskOutput
from app.workspace.base import WorkspaceError


def cwd_file_path(task_id: str, sandbox_tmp_dir: str | None = None) -> str:
    """
    Where the shell writes its final directory: `<tmpdir>/tddagents-<id>-cwd` (upstream
    `claude-<id>-cwd`), or `<sandbox tmp dir>/cwd-<id>` for a sandboxed command, which can write only there.
    """
    if sandbox_tmp_dir is not None:
        return os.path.join(sandbox_tmp_dir, f"cwd-{task_id}")
    return os.path.join(tempfile.gettempdir(), f"tddagents-{task_id}-cwd")


def _track_cwd(context: Any, cwd_file: str, result: ExecResult) -> None:
    """Only a foreground command moves the tracked directory."""
    try:
        if not result.background_task_id:
            new_cwd = Path(cwd_file).read_text(encoding="utf-8").strip()
            if new_cwd and os.path.isdir(new_cwd) and new_cwd != get_cwd(context):
                set_cwd(getattr(context, "workspace", None), new_cwd)
    except OSError:
        pass
    with contextlib.suppress(OSError):
        os.unlink(cwd_file)


async def exec_shell(
    context: Any,
    command: str,
    *,
    timeout_ms: float,
    should_auto_background: bool = False,
    prevent_cwd_changes: bool = False,
    should_use_sandbox: bool = False,
) -> ShellCommand | FinishedShellCommand:
    """`exec`: start `command` and return its `ShellCommand` (or a finished one if it never ran)."""
    task_id = generate_task_id("local_bash")
    task_output = TaskOutput(task_id, get_task_output_path(context, task_id))
    cancel = getattr(context, "cancel", None)
    if cancel is not None and cancel.cancelled:
        return create_aborted_command(task_output)
    workspace = getattr(context, "workspace", None)
    if workspace is None:
        return create_failed_command("No workspace available", task_output)
    sandbox = getattr(context, "sandbox_manager", None) if should_use_sandbox else None
    sandbox_tmp_dir = str(sandbox.temp_dir) if sandbox is not None else None
    cwd_file = None if prevent_cwd_changes else cwd_file_path(task_id, sandbox_tmp_dir)
    fd = open_task_output_for_append(task_output.path)
    try:
        process = await workspace.spawn(command, output_fd=fd, cwd=get_cwd(context), cwd_file=cwd_file,
                                        sandbox=should_use_sandbox)
    except WorkspaceError as exc:
        return create_failed_command(str(exc), task_output)
    finally:
        # The child has its own copy of the descriptor.
        os.close(fd)

    def on_exit(result: ExecResult) -> None:
        if cwd_file is not None:
            _track_cwd(context, cwd_file, result)

    return ShellCommand(process, task_output, timeout_ms, should_auto_background, on_exit=on_exit)
