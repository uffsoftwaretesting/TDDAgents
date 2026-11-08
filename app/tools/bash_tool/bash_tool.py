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

The sandbox (`app/utils/sandbox/`): `dangerouslyDisableSandbox` is in the schema (`BashTool.tsx`), each call
runs sandboxed when `shouldUseSandbox` says so (`should_use_sandbox.py`), its output is annotated with the
sandbox's violations (`annotateStderrWithSandboxFailures`), and the prompt carries upstream's "Command
sandbox" section (`prompt.ts` -> `getSimpleSandboxSection`) for the run's sandbox, computed when the run's
tools are built (upstream computes it per request).
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
from app.tool import (
    BuiltTool,
    SearchOrRead,
    ToolContext,
    ToolResult,
    ValidationResult,
    build_tool,
)
from app.tools.bash_tool import ui
from app.tools.bash_tool.bash_permissions import bash_tool_has_permission_in_sandbox
from app.tools.bash_tool.read_only_validation import is_bash_read_only
from app.tools.bash_tool.should_use_sandbox import should_use_sandbox
from app.tools.bash_tool.utils import (
    reset_cwd_if_outside_project,
    std_err_append_shell_reset_message,
)
from app.types.permissions import PermissionResult, ToolPermissionContext
from app.utils.bash.commands import split_command, split_command_with_operators
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
#: `BashTool.tsx` -> the `dangerouslyDisableSandbox` field's description.
DANGEROUSLY_DISABLE_SANDBOX_DESCRIPTION = (
    "Set this to true to dangerously override sandbox mode and run commands without sandboxing.")
#: `BashTool.tsx` -> the `run_in_background` field's description.
RUN_IN_BACKGROUND_DESCRIPTION = (
    "Set to true to run this command in the background. Use Read to read the output later."
)
#: `toolErrors.ts` -> `INTERRUPT_MESSAGE_FOR_TOOL_USE` is not used here: an aborted command says so
#: in the result (`mapToolResultToToolResultBlockParam`).
ABORTED_COMMAND_ERROR = "<error>Command was aborted before completion</error>"


#: `BashTool.tsx` -> `BASH_SEARCH_COMMANDS`: search commands, collapsed in the REPL.
BASH_SEARCH_COMMANDS = frozenset({"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"})
#: `BASH_READ_COMMANDS`: read/view, analysis and data-processing commands.
BASH_READ_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings",
    "jq", "awk", "cut", "sort", "uniq", "tr",
})
#: `BASH_LIST_COMMANDS`: directory listings, summarized as "Listed N directories".
BASH_LIST_COMMANDS = frozenset({"ls", "tree", "du"})
#: `BASH_SEMANTIC_NEUTRAL_COMMANDS`: output/status commands that never change what a pipeline is.
BASH_SEMANTIC_NEUTRAL_COMMANDS = frozenset({"echo", "printf", "true", "false", ":"})

_NOT_SEARCH_OR_READ = SearchOrRead(is_search=False, is_read=False, is_list=False)


def is_search_or_read_bash_command(command: str) -> SearchOrRead:
    """
    `isSearchOrReadBashCommand`: a command whose every non-neutral part is a search, read or
    listing command (redirect targets and operators skipped) collapses; anything else does not.
    """
    try:
        parts = split_command_with_operators(command)
    except Exception:  # noqa: BLE001 - upstream: unparseable syntax is not a search/read command
        return _NOT_SEARCH_OR_READ
    if not parts:
        return _NOT_SEARCH_OR_READ
    has_search = has_read = has_list = has_non_neutral = skip_next = False
    for part in parts:
        if skip_next:
            skip_next = False
            continue
        if part in (">", ">>", ">&"):
            skip_next = True
            continue
        if part in ("||", "&&", "|", ";"):
            continue
        words = part.split()
        base = words[0] if words else ""
        if not base or base in BASH_SEMANTIC_NEUTRAL_COMMANDS:
            continue
        has_non_neutral = True
        is_search = base in BASH_SEARCH_COMMANDS
        is_read = base in BASH_READ_COMMANDS
        is_list = base in BASH_LIST_COMMANDS
        if not (is_search or is_read or is_list):
            return _NOT_SEARCH_OR_READ
        has_search, has_read, has_list = has_search or is_search, has_read or is_read, has_list or is_list
    if not has_non_neutral:
        return _NOT_SEARCH_OR_READ
    return SearchOrRead(is_search=has_search, is_read=has_read, is_list=has_list)


def bash_search_or_read(input: dict[str, Any]) -> SearchOrRead:
    """`BashTool.isSearchOrReadCommand`: an input without a string command is not one."""
    command = input.get("command")
    if not isinstance(command, str):
        return _NOT_SEARCH_OR_READ
    return is_search_or_read_bash_command(command)


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


def _dedup(items: list[str] | None) -> list[str] | None:
    """`dedup`: first occurrences, in order; an empty or missing list as it is."""
    if not items:
        return items
    return list(dict.fromkeys(items))


def _prepend_bullets(items: list[str | list[str]]) -> list[str]:
    """`prependBullets`: ` - item`, a nested list as `  - item`."""
    lines: list[str] = []
    for item in items:
        if isinstance(item, list):
            lines.extend(f"  - {sub}" for sub in item)
        else:
            lines.append(f" - {item}")
    return lines


def _json(value: Any) -> str:
    """`jsonStringify`: compact JSON, as `JSON.stringify` writes it."""
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


#: `getSimpleSandboxSection`'s override items, when unsandboxed commands are allowed.
SANDBOX_OVERRIDE_ALLOWED: list[str | list[str]] = [
    "You should always default to running commands within the sandbox. Do NOT attempt to set "
    "`dangerouslyDisableSandbox: true` unless:",
    [
        "The user *explicitly* asks you to bypass sandbox",
        "A specific command just failed and you see evidence of sandbox restrictions causing the failure. Note "
        "that commands can fail for many reasons unrelated to the sandbox (missing files, wrong arguments, network "
        "issues, etc.).",
    ],
    "Evidence of sandbox-caused failures includes:",
    [
        '"Operation not permitted" errors for file/network operations',
        "Access denied to specific paths outside allowed directories",
        "Network connection failures to non-whitelisted hosts",
        "Unix socket connection errors",
    ],
    "When you see evidence of sandbox-caused failure:",
    [
        "Immediately retry with `dangerouslyDisableSandbox: true` (don't ask, just do it)",
        "Briefly explain what sandbox restriction likely caused the failure. Be sure to mention that the user can "
        "use the `/sandbox` command to manage restrictions.",
        "This will prompt the user for permission",
    ],
    "Treat each command you execute with `dangerouslyDisableSandbox: true` individually. Even if you have "
    "recently run a command with this setting, you should default to running future commands within the sandbox.",
    "Do not suggest adding sensitive paths like ~/.bashrc, ~/.zshrc, ~/.ssh/*, or credential files to the sandbox "
    "allowlist.",
]
#: ... and when the policy forbids them.
SANDBOX_OVERRIDE_FORBIDDEN: list[str | list[str]] = [
    "All commands MUST run in sandbox mode - the `dangerouslyDisableSandbox` parameter is disabled by policy.",
    "Commands cannot run outside the sandbox under any circumstances.",
    "If a command fails due to sandbox restrictions, work with the user to adjust sandbox settings instead.",
]
SANDBOX_TMPDIR_ITEM = (
    "For temporary files, always use the `$TMPDIR` environment variable. TMPDIR is automatically set to the correct "
    "sandbox-writable directory in sandbox mode. Do NOT use `/tmp` directly - use `$TMPDIR` instead.")


def get_simple_sandbox_section(sandbox: Any) -> str:
    """
    `prompt.ts` -> `getSimpleSandboxSection`: nothing without an enabled sandbox; otherwise the
    "Command sandbox" section with its filesystem and network restrictions as JSON (the per-user temp
    directory shown as `$TMPDIR`), the ignored violations, and the override rules.
    """
    if sandbox is None or not sandbox.is_sandboxing_enabled():
        return ""
    read = sandbox.get_fs_read_config()
    write = sandbox.get_fs_write_config()
    network = sandbox.get_network_restriction_config()
    allow_unix_sockets = sandbox.get_allow_unix_sockets()
    ignore_violations = sandbox.get_ignore_violations()
    temp_dir = str(sandbox.temp_dir)
    filesystem: dict[str, Any] = {
        "read": {"denyOnly": _dedup(read.deny_only),
                 **({"allowWithinDeny": _dedup(read.allow_within_deny)} if read.allow_within_deny is not None
                    else {})},
        "write": {"allowOnly": ["$TMPDIR" if p == temp_dir else p for p in dict.fromkeys(write.allow_only)],
                  "denyWithinAllow": _dedup(write.deny_within_allow)},
    }
    network_config: dict[str, Any] = {}
    if network.allowed_hosts:
        network_config["allowedHosts"] = _dedup(network.allowed_hosts)
    if network.denied_hosts:
        network_config["deniedHosts"] = _dedup(network.denied_hosts)
    if allow_unix_sockets is not None:
        network_config["allowUnixSockets"] = _dedup(allow_unix_sockets)
    restrictions = [f"Filesystem: {_json(filesystem)}"]
    if network_config:
        restrictions.append(f"Network: {_json(network_config)}")
    if ignore_violations:
        restrictions.append(f"Ignored violations: {_json(ignore_violations)}")
    overrides = SANDBOX_OVERRIDE_ALLOWED if sandbox.are_unsandboxed_commands_allowed() else SANDBOX_OVERRIDE_FORBIDDEN
    return "\n".join([
        "",
        "## Command sandbox",
        "By default, your command will be run in a sandbox. This sandbox controls which directories and network "
        "hosts commands may access or modify without an explicit override.",
        "",
        "The sandbox has the following restrictions:",
        "\n".join(restrictions),
        "",
        *_prepend_bullets([*overrides, SANDBOX_TMPDIR_ITEM]),
    ])


def get_bash_prompt(sandbox: Any = None) -> str:
    """
    `BashTool/prompt.ts` -> `getSimplePrompt` (external build): `prompt.md`, with the timeouts
    filled in from `src/utils/timeouts.ts`, as upstream interpolates them, and the run's sandbox
    section (`get_simple_sandbox_section`; its own line, empty without a sandbox).
    """
    text = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    note = get_background_usage_note()
    if note is None:
        text = text.replace(" - {{BACKGROUND_NOTE}}\n", "")
    max_ms, default_ms = get_max_bash_timeout_ms(), get_default_bash_timeout_ms()
    section = get_simple_sandbox_section(sandbox)
    return render_prompt(text, {
        "MAX_TIMEOUT_MS": max_ms, "MAX_TIMEOUT_MIN": f"{max_ms / 60000:g}",
        "DEFAULT_TIMEOUT_MS": default_ms, "DEFAULT_TIMEOUT_MIN": f"{default_ms / 60000:g}",
        "BACKGROUND_NOTE": note or "",
        "SANDBOX_SECTION": f"{section}\n" if section else "",
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
    override = args.get("dangerouslyDisableSandbox")
    if override is not None and not isinstance(override, bool):
        return ValidationResult(valid=False, message="dangerouslyDisableSandbox must be a boolean.")
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
    """`BashTool.checkPermissions` -> `bashToolHasPermission`, with the run's sandbox."""
    permission_context, cwd = bash_permission_inputs(context)
    return bash_tool_has_permission_in_sandbox(args, permission_context, cwd,
                                               sandbox=getattr(context, "sandbox_manager", None),
                                               original_cwd=permission_cwd(context))


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
    kills it (upstream's abort signal), unless the cancel is a submit-interrupt.
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
        # `ShellCommand#abortHandler`: on 'interrupt' (the user submitted a new message) the
        # command is not killed; it runs on and its output reaches the model.
        if cancel is not None and cancel.cancelled and cancel.reason != "interrupt":
            shell.kill()
        if not registered and (loop.time() - started) * 1000 >= PROGRESS_THRESHOLD_MS:
            register()
            registered = True
        await asyncio.wait({shell.result}, timeout=CANCEL_POLL_SECONDS)


def bash_output(stdout: str, stderr: str, interrupted: bool, background_task_id: str | None,
                dangerously_disable_sandbox: bool | None = None) -> dict[str, Any]:
    """Upstream's Bash `Out`: what the result rendering and the SDK's `tool_use_result` read."""
    data: dict[str, Any] = {"stdout": stdout, "stderr": stderr, "interrupted": interrupted}
    if background_task_id:
        data["backgroundTaskId"] = background_task_id
    if dangerously_disable_sandbox is not None:
        data["dangerouslyDisableSandbox"] = dangerously_disable_sandbox
    return data


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


def build_bash_tool(sandbox: Any = None) -> BuiltTool:
    """The Bash tool; `sandbox` (the run's, when it has one) only shapes the prompt's sandbox section."""

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
        run_sandbox = getattr(context, "sandbox_manager", None)
        shell = await exec_shell(context, command, timeout_ms=timeout_ms,
                                 should_auto_background=should_auto_background,
                                 should_use_sandbox=should_use_sandbox(input_args, run_sandbox))
        override = input_args.get("dangerouslyDisableSandbox") if "dangerouslyDisableSandbox" in input_args else None
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
            return ToolResult(content=background_info(state["background"], context),
                              metadata=bash_output("", "", False, state["background"]))

        def register() -> None:
            state["foreground"] = register_foreground(context, command=command, description=description,
                                                      shell_command=shell, tool_use_id=tool_use_id, agent_id=agent_id)

        result = await _wait_for_result(shell, context, backgrounded, register)
        if result is None:
            return ToolResult(content=background_info(state["background"], context),
                              metadata=bash_output("", "", False, state["background"]))
        if state["foreground"] is not None:
            if result.background_task_id:
                mark_task_notified(result.background_task_id, context.set_app_state)
            unregister_foreground(state["foreground"], context.set_app_state)
        if result.pre_spawn_error:
            return ToolResult(content=f"Execution error: {result.pre_spawn_error}", is_error=True)

        stderr = result.stderr
        if reset_cwd_if_outside_project(context):
            stderr = std_err_append_shell_reset_message(stderr, context)
        # `annotateStderrWithSandboxFailures` (stderr is merged into the output file).
        output = result.stdout
        if run_sandbox is not None:
            output = run_sandbox.annotate_stderr_with_sandbox_failures(command, output)
        stdout = strip_leading_blank_lines(output)
        persisted = persist_large_output(result, context)
        if persisted is not None:
            preview, has_more = generate_preview(stdout)
            stdout = build_large_tool_result_message(persisted[1], persisted[0], preview, has_more)
        # An interrupted command is never a `ShellError` here (upstream's `isInterrupt` is the
        # submit-interrupt subset of this).
        if result.code != 0 and not result.interrupted:
            # `ShellError` through `formatError`: `Exit code N`, then stderr, then the output.
            content = "\n".join(part for part in (f"Exit code {result.code}", stderr.strip(), stdout) if part)
            return ToolResult(content=content, exit_code=result.code, is_error=True,
                              metadata=bash_output(stdout, stderr, result.interrupted, None, override))
        content = bash_result_content(stdout, stderr, result.interrupted, "")
        return ToolResult(content=content or "Command executed successfully (no output).", exit_code=result.code,
                          is_error=result.interrupted,
                          metadata=bash_output(stdout, stderr, result.interrupted, None, override))

    return build_tool(
        user_facing_name=ui.user_facing_name,
        render_tool_use_message=ui.render_tool_use_message,
        get_tool_use_summary=ui.get_tool_use_summary,
        get_activity_description=ui.get_activity_description,
        render_tool_result_message=ui.render_tool_result_message,
        is_search_or_read_command=bash_search_or_read,
        name="Bash",
        prompt=get_bash_prompt(sandbox),
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
                "dangerouslyDisableSandbox": {"type": "boolean",
                                              "description": DANGEROUSLY_DISABLE_SANDBOX_DESCRIPTION},
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
