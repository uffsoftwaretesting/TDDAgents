"""
The four execution tools: Bash, BashOutput, KillShell, RunCode.

**`Bash` decides read-only per input.** `Bash("ls")` is read-only and parallelizable;
`Bash("rm -rf /")` is neither. A per-tool boolean cannot express that, which is why
`is_read_only` takes the arguments and `is_concurrency_safe` simply delegates to it —
exactly as `BashTool.isReadOnly` does upstream. The same predicate drives
`required_capability`, and that is what lets the read-only researcher hold `Bash` at all:
it may run `grep` and `ls`, and is refused anything else by the capability gate rather
than by a sentence in its prompt.

The command parse is deliberately conservative. Anything it cannot prove safe — a pipe
into an unknown program, a subshell, a redirect, an unrecognized binary — is treated as
not read-only. A false "unsafe" costs parallelism; a false "safe" would hand a
`read_only` agent arbitrary execution.

**`BashOutput` and `KillShell` are sandbox-pinned**, bypassing the workspace router the
way `RunTests` does. E2B owns the process handles, and `LocalWorkspace` has deliberately
not grown a process table for a capability no agent in the locked roster can reach.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import (
    AnyTool,
    BASH_MAX_RESULT_CHARS,
    Capability,
    ToolContext,
    ToolResult,
    build_tool,
    err,
    ok,
)
from app.workspace.base import WorkspaceError

#: Programs that only read. Everything outside this set makes a command non-read-only.
READ_ONLY_COMMANDS = frozenset({
    "awk", "basename", "cat", "cksum", "comm", "cut", "date", "df", "diff", "dirname",
    "du", "echo", "env", "file", "find", "grep", "head", "hostname", "id", "join", "less",
    "ls", "md5sum", "nl", "od", "printenv", "printf", "ps", "pwd", "readlink", "realpath",
    "rev", "sha1sum", "sha256sum", "sort", "stat", "tail", "tr", "true", "uname", "uniq",
    "wc", "which", "whoami", "xxd",
})

#: Shell metacharacters that make a command impossible to classify by inspecting argv.
#: A redirect writes, a subshell can do anything, and `;`/`&&` chain further commands.
UNSAFE_SHELL_PATTERN = re.compile(r"[;&><`]|\$\(|\|\||\bsudo\b")

#: Multi-word programs whose first argument decides whether the call reads or writes.
_SUBCOMMAND_READERS = {
    "git": frozenset({"status", "log", "diff", "show", "branch", "blame", "describe"}),
    "pip": frozenset({"list", "show", "freeze"}),
    "python": frozenset(),  # never classifiable: `python -c` runs arbitrary code
}


def is_read_only_command(command: str) -> bool:
    """
    Whether `command` can be proven to only read.

    Fails closed on anything it cannot parse or does not recognize.
    """
    text = command.strip()
    if not text or UNSAFE_SHELL_PATTERN.search(text):
        return False

    # A pipeline is read-only only if every stage is.
    if "|" in text:
        return all(is_read_only_command(stage) for stage in text.split("|"))

    try:
        argv = shlex.split(text)
    except ValueError:
        return False
    if not argv:
        return False

    # Strip leading VAR=value assignments, matching how argv is normalized upstream.
    while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]):
        argv = argv[1:]
    if not argv:
        return False

    program = argv[0].rsplit("/", maxsplit=1)[-1]

    if program in _SUBCOMMAND_READERS:
        return len(argv) > 1 and argv[1] in _SUBCOMMAND_READERS[program]

    return program in READ_ONLY_COMMANDS


# ─────────────────────────────────────────────────────────────────────────────
# Bash
# ─────────────────────────────────────────────────────────────────────────────

class BashArgs(BaseModel):
    command: str = Field(description="The shell command to run.")
    timeout: float | None = Field(
        default=None, description="Seconds to allow. Defaults to Config.COMMAND_TIMEOUT."
    )
    run_in_background: bool = Field(
        default=False,
        description="Start the command without waiting. Poll it with BashOutput.",
    )


BASH_PROMPT = """Runs a shell command from the workspace root.

A non-zero exit code is a result, not a failure: the exit code, stdout and stderr all come
back for you to reason about. Only infrastructure problems are reported as tool errors.

Set run_in_background for a long-running process (a dev server, a watcher). You get a
command id back; read its output with BashOutput and stop it with KillShell. Background
commands run in the sandbox only.

Prefer ReadFile, ListDir, Glob and Grep over `cat`, `ls`, `find` and `grep` — they are
faster, they page properly, and they work against either workspace."""


def _bash(args: BashArgs, ctx: ToolContext) -> ToolResult:
    if args.run_in_background:
        return _bash_background(args, ctx)

    result = ctx.workspace.execute(args.command, timeout=args.timeout)
    body = _render_command_result(result.stdout, result.stderr, result.exit_code)
    return ok(body, exit_code=result.exit_code)


def _bash_background(args: BashArgs, ctx: ToolContext) -> ToolResult:
    adapter = sandbox_adapter(ctx)
    if adapter is None:
        return err("Background commands require the sandbox workspace.")

    command = adapter.start_background(args.command)
    ctx.background_commands[command.id] = args.command
    return ok(
        f"Started in the background with id {command.id}.\n"
        f'Read its output with BashOutput(command_id="{command.id}") and stop it '
        f'with KillShell(command_id="{command.id}").'
    )


def _render_command_result(stdout: str, stderr: str, exit_code: int) -> str:
    parts = []
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append(f"--- stderr ---\n{stderr.rstrip()}")
    if not parts:
        parts.append("(no output)")
    parts.append(f"--- exit code: {exit_code} ---")
    return "\n".join(parts)


def _bash_capability(args: BashArgs) -> Capability:
    # A background start is never a read, whatever the command says.
    if args.run_in_background:
        return Capability.EXECUTE
    return Capability.READ if is_read_only_command(args.command) else Capability.EXECUTE


def _bash_is_read_only(args: BashArgs) -> bool:
    return not args.run_in_background and is_read_only_command(args.command)


Bash = build_tool(
    name="Bash",
    args_schema=BashArgs,
    prompt=BASH_PROMPT,
    call=_bash,
    description=lambda args: args.command[:80],
    max_result_chars=BASH_MAX_RESULT_CHARS,
    is_read_only=_bash_is_read_only,
    is_concurrency_safe=_bash_is_read_only,
    is_destructive=lambda args: not _bash_is_read_only(args),
    required_capability=_bash_capability,
)


# ─────────────────────────────────────────────────────────────────────────────
# BashOutput and KillShell — sandbox-pinned
# ─────────────────────────────────────────────────────────────────────────────

def sandbox_adapter(ctx: ToolContext) -> Any:
    """
    Digs the E2B adapter out of whichever Workspace the router resolved.

    `E2BWorkspace` exposes it directly; `DualWorkspace` keeps it behind `.sandbox`. A
    `LocalWorkspace` has none, and the caller turns that into a tool error rather than an
    exception — an agent asking for a sandbox-only capability it was not granted should
    read why, not see a stack trace.
    """
    workspace = ctx.workspace
    adapter = getattr(workspace, "adapter", None)
    if adapter is not None:
        return adapter
    sandbox = getattr(workspace, "sandbox", None)
    return getattr(sandbox, "adapter", None) if sandbox is not None else None


class BashOutputArgs(BaseModel):
    command_id: str = Field(description="The id returned by Bash(run_in_background=true).")


def _bash_output(args: BashOutputArgs, ctx: ToolContext) -> ToolResult:
    adapter = sandbox_adapter(ctx)
    if adapter is None:
        return err("Background commands require the sandbox workspace.")

    command = adapter.get_background(args.command_id)
    if command is None:
        return err(f"No background command with id {args.command_id}.")

    stdout, stderr = command.drain()
    if not stdout and not stderr:
        state = "finished" if command.finished else "still running"
        return ok(f"No new output from {args.command_id} ({state}).")

    return ok(_render_command_result(stdout, stderr, command.exit_code or 0))


BashOutput = build_tool(
    name="BashOutput",
    args_schema=BashOutputArgs,
    prompt=(
        "Reads output produced by a background command since the last time you read it. "
        "Output already returned is not repeated, so polling repeatedly gives you the "
        "stream in order rather than a growing transcript. Sandbox only."
    ),
    call=_bash_output,
    description=lambda args: f"Read output of {args.command_id}",
    max_result_chars=BASH_MAX_RESULT_CHARS,
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
    required_workspace=lambda args: "sandbox",
)


class KillShellArgs(BaseModel):
    command_id: str = Field(description="The id of the background command to stop.")


def _kill_shell(args: KillShellArgs, ctx: ToolContext) -> ToolResult:
    adapter = sandbox_adapter(ctx)
    if adapter is None:
        return err("Background commands require the sandbox workspace.")

    if not adapter.kill_command(args.command_id):
        return err(f"No background command with id {args.command_id}.")

    ctx.background_commands.pop(args.command_id, None)
    return ok(f"Stopped {args.command_id}.")


KillShell = build_tool(
    name="KillShell",
    args_schema=KillShellArgs,
    prompt=(
        "Stops a background command started by Bash. Killing a command that has already "
        "exited is not an error. Sandbox only."
    ),
    call=_kill_shell,
    description=lambda args: f"Kill {args.command_id}",
    is_destructive=lambda args: True,
    required_capability=lambda args: Capability.EXECUTE,
    required_workspace=lambda args: "sandbox",
)


# ─────────────────────────────────────────────────────────────────────────────
# RunCode
# ─────────────────────────────────────────────────────────────────────────────

class RunCodeArgs(BaseModel):
    code: str = Field(description="Code to run in the sandbox REPL.")
    language: str | None = Field(default=None, description="Defaults to Python.")


def _run_code(args: RunCodeArgs, ctx: ToolContext) -> ToolResult:
    adapter = sandbox_adapter(ctx)
    if adapter is None:
        return err("RunCode requires the sandbox workspace.")

    try:
        execution = adapter.run_code(args.code, language=args.language)
    except WorkspaceError as exc:
        return err(f"RunCode failed: {exc}")

    return ok(_render_execution(execution))


def _render_execution(execution: Any) -> str:
    """
    Flattens an e2b_code_interpreter Execution into text.

    Read defensively with getattr: this is the one place a provider object reaches a tool,
    and the Execution shape is the part of the SDK most likely to gain fields.
    """
    parts: list[str] = []

    logs = getattr(execution, "logs", None)
    for stream in ("stdout", "stderr"):
        entries = getattr(logs, stream, None) if logs is not None else None
        if entries:
            body = "".join(entries) if isinstance(entries, list) else str(entries)
            if body.strip():
                label = "" if stream == "stdout" else "--- stderr ---\n"
                parts.append(f"{label}{body.rstrip()}")

    error = getattr(execution, "error", None)
    if error is not None:
        name = getattr(error, "name", "Error")
        value = getattr(error, "value", "")
        parts.append(f"--- error ---\n{name}: {value}")

    for result in getattr(execution, "results", None) or []:
        text = getattr(result, "text", None)
        if text:
            parts.append(str(text).rstrip())

    return "\n".join(parts) if parts else "(no output)"


RunCode = build_tool(
    name="RunCode",
    args_schema=RunCodeArgs,
    prompt=(
        "Runs code in the sandbox's stateful REPL. State persists between calls, so a "
        "variable or import from an earlier call is still there in the next one — useful "
        "for exploring data or trying an approach before committing it to a file.\n\n"
        "This is not how you run the test suite; use RunTests for that. Sandbox only."
    ),
    call=_run_code,
    description=lambda args: f"Run {len(args.code)} chars of code",
    required_capability=lambda args: Capability.EXECUTE,
    required_workspace=lambda args: "sandbox",
)


EXEC_TOOLS: list[AnyTool] = [Bash, BashOutput, KillShell, RunCode]
