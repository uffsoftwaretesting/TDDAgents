"""
The loop's `Bash` tool: one shell command, run through the context's workspace.

Ported from `reference/claude-code/src/tools/BashTool/BashTool.tsx` -> `BashTool`:

- Input is `BashInput` from `sdk-tools.d.ts`: `command`, optional `timeout` in
  milliseconds, optional `description`.
- `checkPermissions` is `bashToolHasPermission` (`app/tools/bash_tool/bash_permissions.py`):
  Bash rules from settings (exact, `prefix:*`, wildcard), the injection battery, output
  redirection checks, acceptEdits mode, and read-only auto-allow. A command nothing
  resolves returns 'passthrough'; the runtime gate (`has_permissions_to_use_tool`) turns
  that into 'ask', and a headless context (`should_avoid_permission_prompts`) into 'deny'.
- `isReadOnly` is upstream's `checkReadOnlyConstraints` (`tools/bash_tool/read_only_validation.py`), and
  `isConcurrencySafe` is `isReadOnly`.
- Timeouts follow `src/utils/timeouts.ts`: default 120000 ms, overridable through
  `BASH_DEFAULT_TIMEOUT_MS`; `timeout || default` exactly as `BashTool` computes it.

The working directory for path checks is the workspace's `root` when it has one (the
local workspace); otherwise the process working directory, upstream's `getCwd()`.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.tasks.local_shell_task.local_shell_task import (
    background_existing_foreground_task,
    mark_task_notified,
    register_foreground,
    spawn_shell_task,
    unregister_foreground,
)
from app.tool import BuiltTool, ToolContext, ToolResult, ValidationResult, build_tool
from app.tools.bash_tool import ui
from app.tools.bash_tool.bash_permissions import bash_tool_has_permission
from app.tools.bash_tool.read_only_validation import is_bash_read_only
from app.tools.bash_tool.utils import (
    reset_cwd_if_outside_project,
    std_err_append_shell_reset_message,
)
from app.types.permissions import PermissionResult, ToolPermissionContext
from app.utils.bash.commands import split_command
from app.utils.cwd import get_cwd
from app.utils.frontmatter_parser import parse_markdown_frontmatter, render_prompt
from app.utils.permissions.filesystem import permission_cwd
from app.utils.permissions.permissions import extract_permission_context
from app.utils.shell.shell import exec_shell
from app.utils.shell_command import ExecResult
from app.utils.task.disk_output import get_task_output_path
from app.utils.tool_result_storage import (
    build_large_tool_result_message,
    generate_preview,
    tool_results_dir,
)

PROMPT_PATH = Path(__file__).with_name("prompt.md")

#: `BashTool.tsx` -> `maxResultSizeChars`. Lower than the global clamp, so it is the one
#: that bites: shell output is the most likely thing to flood a context window.
BASH_MAX_RESULT_SIZE_CHARS = 30_000

#: `src/utils/timeouts.ts` -> `DEFAULT_TIMEOUT_MS` / `MAX_TIMEOUT_MS`.
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000

#: `BashTool.tsx` -> `PROGRESS_THRESHOLD_MS`: a command still running after this is registered as a
#: foreground task, so a later timeout can background it in place.
PROGRESS_THRESHOLD_MS = 2000
#: `DISALLOWED_AUTO_BACKGROUND_COMMANDS`.
DISALLOWED_AUTO_BACKGROUND_COMMANDS = ("sleep",)
#: How often the foreground wait looks at the run's cancel token (it has no listeners).
CANCEL_POLL_SECONDS = 0.05
#: `BashTool.tsx` -> the persisted copy of a large output is truncated to this.
MAX_PERSISTED_SIZE = 64 * 1024 * 1024
#: `prompt.ts` -> `getBackgroundUsageNote`.
BACKGROUND_USAGE_NOTE = (
    "You can use the `run_in_background` parameter to run the command in the background. Only use this if you "
    "don't need the result immediately and are OK being notified when the command completes later. You do not "
    "need to check the output right away - you'll be notified when it finishes. You do not need to use '&' at "
    "the end of the command when using this parameter."
)
#: `BashTool.tsx` -> the `run_in_background` field's description.
RUN_IN_BACKGROUND_DESCRIPTION = (
    "Set to true to run this command in the background. Use Read to read the output later."
)
#: `toolErrors.ts` -> `INTERRUPT_MESSAGE_FOR_TOOL_USE` is not used here: an aborted command says so
#: in the result (`mapToolResultToToolResultBlockParam`).
ABORTED_COMMAND_ERROR = "<error>Command was aborted before completion</error>"


def _positive_int(value: str | None) -> int | None:
    """`parseInt(v, 10)` accepted only when it is a number greater than zero."""
    match = re.match(r"\s*([+-]?[0-9]+)", value or "")
    if match is None:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def get_default_bash_timeout_ms(env: Mapping[str, str] | None = None) -> int:
    """`getDefaultBashTimeoutMs`: `BASH_DEFAULT_TIMEOUT_MS` when a positive integer."""
    source = os.environ if env is None else env
    return _positive_int(source.get("BASH_DEFAULT_TIMEOUT_MS")) or DEFAULT_TIMEOUT_MS


def get_max_bash_timeout_ms(env: Mapping[str, str] | None = None) -> int:
    """`getMaxBashTimeoutMs`: never below the default."""
    source = os.environ if env is None else env
    default = get_default_bash_timeout_ms(source)
    override = _positive_int(source.get("BASH_MAX_TIMEOUT_MS"))
    if override is not None:
        return max(override, default)
    return max(MAX_TIMEOUT_MS, default)


def get_bash_prompt() -> str:
    """
    `BashTool/prompt.ts` -> `getSimplePrompt` (external build): `prompt.md`, with the timeouts
    filled in from `src/utils/timeouts.ts`, as upstream interpolates them.
    """
    text = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    note = get_background_usage_note()
    if note is None:
        text = text.replace(" - {{BACKGROUND_NOTE}}\n", "")
    max_ms, default_ms = get_max_bash_timeout_ms(), get_default_bash_timeout_ms()
    return render_prompt(text, {
        "MAX_TIMEOUT_MS": max_ms, "MAX_TIMEOUT_MIN": f"{max_ms / 60000:g}",
        "DEFAULT_TIMEOUT_MS": default_ms, "DEFAULT_TIMEOUT_MIN": f"{default_ms / 60000:g}",
        "BACKGROUND_NOTE": note or "",
    }, strip_comments=False)


def is_background_tasks_disabled() -> bool:
    """`isBackgroundTasksDisabled` (`TDDAGENTS_DISABLE_BACKGROUND_TASKS`, the Agent tool's flag)."""
    from app.tools.agent_tool.agent_tool import is_background_tasks_disabled as disabled

    return disabled()


def get_background_usage_note() -> str | None:
    """`getBackgroundUsageNote`: the `run_in_background` note, unless background tasks are off."""
    if is_background_tasks_disabled():
        return None
    return BACKGROUND_USAGE_NOTE


def is_autobackgrounding_allowed(command: str) -> bool:
    """`isAutobackgroundingAllowed`: a timeout may background anything but `sleep`."""
    parts = split_command(command)
    if not parts:
        return True
    base = parts[0].strip().split(" ")[0] if parts[0].strip() else ""
    return not base or base not in DISALLOWED_AUTO_BACKGROUND_COMMANDS


def validate_bash_input(args: dict[str, Any], context: ToolContext) -> ValidationResult:
    """
    The schema half of upstream's input validation (zod): `command` is a string,
    `timeout` a number, `description` a string. `BashTool.validateInput` itself only
    carries the feature-gated sleep detector, which this build does not enable.
    """
    if not isinstance(args.get("command"), str):
        return ValidationResult(valid=False, message="command must be a string.")
    timeout = args.get("timeout")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))):
        return ValidationResult(valid=False, message="timeout must be a number of milliseconds.")
    description = args.get("description")
    if description is not None and not isinstance(description, str):
        return ValidationResult(valid=False, message="description must be a string.")
    background = args.get("run_in_background")
    if background is not None and not isinstance(background, bool):
        return ValidationResult(valid=False, message="run_in_background must be a boolean.")
    return ValidationResult(valid=True)


def bash_permission_inputs(context: ToolContext) -> tuple[ToolPermissionContext, str]:
    """
    What `bashToolHasPermission` resolves against: the tracked working directory (`getCwd()`),
    with the original one kept among the working directories, as upstream's
    `allWorkingDirectories` always includes `getOriginalCwd()`.
    """
    permission_context = extract_permission_context(context)
    cwd, original = get_cwd(context), permission_cwd(context)
    if cwd != original and original not in permission_context.additional_working_directories:
        permission_context = replace(permission_context, additional_working_directories=(
            original, *permission_context.additional_working_directories))
    return permission_context, cwd


async def check_bash_permissions(args: dict[str, Any], context: ToolContext) -> PermissionResult:
    """`BashTool.checkPermissions` -> `bashToolHasPermission`."""
    permission_context, cwd = bash_permission_inputs(context)
    return bash_tool_has_permission(str(args.get("command") or ""), permission_context, cwd)


def bash_permission_matcher(args: Mapping[str, Any]) -> Callable[[str], bool]:
    """
    `BashTool.preparePermissionMatcher`, as the external build runs it: it calls
    `parseForSecurity`, which is `parse-unavailable` without `feature('TREE_SITTER_BASH')`
    ("ant-only until pentest", `utils/bash/parser.ts`), and on anything but a simple parse the
    matcher fails safe — every content condition matches, so the hook always runs. Hook `if`
    filtering is "no match -> skip the hook", so matching everything can only run a hook
    more often, never skip one; a hook's allow is still re-checked by the TDD phase tier.
    """
    return lambda pattern: True


def background_info(task_id: str | None, context: ToolContext) -> str:
    """`mapToolResultToToolResultBlockParam`'s line for a backgrounded command."""
    if task_id is None:
        return ""
    output = get_task_output_path(context, task_id)
    return f"Command running in background with ID: {task_id}. Output is being written to: {output}"


async def _wait_for_result(
    shell: Any, context: ToolContext, backgrounded: asyncio.Event, register: Callable[[], None]
) -> ExecResult | None:
    """
    The foreground wait: the result, or `None` once the command went to the background. After
    `PROGRESS_THRESHOLD_MS` the command is registered as a foreground task; a cancelled run
    kills it (upstream's abort signal).
    """
    loop = asyncio.get_running_loop()
    registered = False
    started = loop.time()
    while True:
        if shell.result.done():
            return shell.result.result()  # type: ignore[no-any-return]
        if backgrounded.is_set():
            return None
        cancel = getattr(context, "cancel", None)
        if cancel is not None and cancel.cancelled:
            shell.kill()
        if not registered and (loop.time() - started) * 1000 >= PROGRESS_THRESHOLD_MS:
            register()
            registered = True
        await asyncio.wait({shell.result}, timeout=CANCEL_POLL_SECONDS)


def strip_leading_blank_lines(text: str) -> str:
    """`mapToolResultToToolResultBlockParam`: leading blank lines removed, the end trimmed."""
    return re.sub(r"^(\s*\n)+", "", text).rstrip()


def persist_large_output(result: ExecResult, context: ToolContext) -> tuple[str, int] | None:
    """
    `BashTool.call`: an output larger than what `get_stdout` read is kept, whole, in the run's
    tool-results directory (hard-linked, else copied; truncated past `MAX_PERSISTED_SIZE`).
    """
    if not result.output_file_path or not result.output_task_id:
        return None
    try:
        size = os.path.getsize(result.output_file_path)
        dest = tool_results_dir(context.run_id or "session", permission_cwd(context)) / f"{result.output_task_id}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if size > MAX_PERSISTED_SIZE:
            os.truncate(result.output_file_path, MAX_PERSISTED_SIZE)
        try:
            os.link(result.output_file_path, dest)
        except OSError:
            shutil.copyfile(result.output_file_path, dest)
    except OSError:
        return None
    return str(dest), size


def bash_result_content(stdout: str, stderr: str, interrupted: bool, background_info: str) -> str:
    """`mapToolResultToToolResultBlockParam`: `[stdout, error, background info]`, non-empty, by line."""
    error = stderr.strip()
    if interrupted:
        error = f"{error}\n{ABORTED_COMMAND_ERROR}" if stderr else ABORTED_COMMAND_ERROR
    return "\n".join(part for part in (stdout, error, background_info) if part)


def build_bash_tool() -> BuiltTool:
    async def call_bash(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        """
        `BashTool.call` / `runShellCommand`: start the command (`exec_shell`), then either hand it
        to the background at once (`run_in_background`), or wait for it — registering it as a
        foreground task after `PROGRESS_THRESHOLD_MS`, so a timeout backgrounds it in place
        instead of killing it (unless it is `sleep`). The run's cancel token (Esc) kills it.
        """
        command = str(input_args.get("command") or "")
        if getattr(context, "workspace", None) is None:
            return ToolResult(content="No workspace available", is_error=True)
        description = str(input_args.get("description") or command)
        disabled = is_background_tasks_disabled()
        timeout_ms = min(input_args.get("timeout") or get_default_bash_timeout_ms(), get_max_bash_timeout_ms())
        should_auto_background = not disabled and is_autobackgrounding_allowed(command)
        shell = await exec_shell(context, command, timeout_ms=timeout_ms,
                                 should_auto_background=should_auto_background)
        tool_use_id = getattr(context, "tool_use_id", None)
        agent_id = getattr(context, "agent_id", None)
        state: dict[str, str | None] = {"background": None, "foreground": None}
        backgrounded = asyncio.Event()

        def start_backgrounding(_background: Callable[[str], bool] | None = None) -> None:
            """`startBackgrounding`."""
            foreground = state["foreground"]
            if foreground is not None:
                if background_existing_foreground_task(context, foreground, shell, description, tool_use_id):
                    state["background"] = foreground
            else:
                state["background"] = spawn_shell_task(context, command=command, description=description,
                                                       shell_command=shell, tool_use_id=tool_use_id,
                                                       agent_id=agent_id)
            backgrounded.set()

        if should_auto_background:
            shell.on_timeout(start_backgrounding)
        if bool(input_args.get("run_in_background")) and not disabled:
            start_backgrounding()
            return ToolResult(content=background_info(state["background"], context))

        def register() -> None:
            state["foreground"] = register_foreground(context, command=command, description=description,
                                                      shell_command=shell, tool_use_id=tool_use_id, agent_id=agent_id)

        result = await _wait_for_result(shell, context, backgrounded, register)
        if result is None:
            return ToolResult(content=background_info(state["background"], context))
        if state["foreground"] is not None:
            if result.background_task_id:
                mark_task_notified(result.background_task_id, context.set_app_state)
            unregister_foreground(state["foreground"], context.set_app_state)
        if result.pre_spawn_error:
            return ToolResult(content=f"Execution error: {result.pre_spawn_error}", is_error=True)

        stderr = result.stderr
        if reset_cwd_if_outside_project(context):
            stderr = std_err_append_shell_reset_message(stderr, context)
        stdout = strip_leading_blank_lines(result.stdout)
        persisted = persist_large_output(result, context)
        if persisted is not None:
            preview, has_more = generate_preview(stdout)
            stdout = build_large_tool_result_message(persisted[1], persisted[0], preview, has_more)
        if result.code != 0 and not result.interrupted:
            # `ShellError` through `formatError`: `Exit code N`, then stderr, then the output.
            content = "\n".join(part for part in (f"Exit code {result.code}", stderr.strip(), stdout) if part)
            return ToolResult(content=content, exit_code=result.code, is_error=True)
        content = bash_result_content(stdout, stderr, result.interrupted, "")
        return ToolResult(content=content or "Command executed successfully (no output).", exit_code=result.code,
                          is_error=result.interrupted)

    return build_tool(
        user_facing_name=ui.user_facing_name,
        render_tool_use_message=ui.render_tool_use_message,
        name="Bash",
        prompt=get_bash_prompt(),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to execute"},
                "timeout": {
                    "type": "number",
                    "description": f"Optional timeout in milliseconds (max {get_max_bash_timeout_ms()})",
                },
                "description": {
                    "type": "string",
                    "description": "Clear, concise description of what this command does in active voice.",
                },
                **({} if is_background_tasks_disabled() else {
                    "run_in_background": {"type": "boolean", "description": RUN_IN_BACKGROUND_DESCRIPTION},
                }),
            },
            "required": ["command"],
        },
        description=lambda args: args.get("description") or "Run shell command",
        max_result_size_chars=BASH_MAX_RESULT_SIZE_CHARS,
        is_read_only=is_bash_read_only,
        is_concurrency_safe=is_bash_read_only,
        validate_input=validate_bash_input,
        check_permissions=check_bash_permissions,
        call=call_bash,
        prepare_permission_matcher=bash_permission_matcher,
    )
