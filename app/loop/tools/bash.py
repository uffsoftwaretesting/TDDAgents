"""
The loop's `Bash` tool: one shell command, run through the context's workspace.

Ported from `reference/claude-code/src/tools/BashTool/BashTool.tsx` -> `BashTool`:

- Input is `BashInput` from `sdk-tools.d.ts`: `command`, optional `timeout` in
  milliseconds, optional `description`.
- `checkPermissions` is `bashToolHasPermission` (`app/loop/permissions/bash_permissions.py`):
  Bash rules from settings (exact, `prefix:*`, wildcard), the injection battery, output
  redirection checks, acceptEdits mode, and read-only auto-allow. A command nothing
  resolves returns 'passthrough'; the runtime gate (`has_permissions_to_use_tool`) turns
  that into 'ask', and a headless context (`should_avoid_permission_prompts`) into 'deny'.
- `isReadOnly` is upstream's `checkReadOnlyConstraints` (`bash_read_only.py`), and
  `isConcurrencySafe` is `isReadOnly`.
- Timeouts follow `src/utils/timeouts.ts`: default 120000 ms, overridable through
  `BASH_DEFAULT_TIMEOUT_MS`; `timeout || default` exactly as `BashTool` computes it.

The working directory for path checks is the workspace's `root` when it has one (the
local workspace); otherwise the process working directory, upstream's `getCwd()`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

from app.loop.context import ToolContext
from app.loop.permissions.bash_permissions import bash_tool_has_permission
from app.loop.permissions.bash_read_only import is_bash_read_only
from app.loop.permissions.rules import extract_permission_context
from app.loop.permissions.types import PermissionResult
from app.loop.prompts.loader import parse_markdown_frontmatter
from app.loop.tools.base import BuiltTool, build_tool
from app.loop.tools.types import ToolResult, ValidationResult

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "tools" / "bash.md"
BASH_PROMPT = (
    parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))[1]
    if PROMPT_PATH.exists()
    else "Runs a command in a bash shell."
)

#: `src/utils/timeouts.ts` -> `DEFAULT_TIMEOUT_MS` / `MAX_TIMEOUT_MS`.
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000


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
    return ValidationResult(valid=True)


def permission_cwd(context: ToolContext) -> str:
    """The directory path checks resolve against: the workspace root, else `os.getcwd()`."""
    root = getattr(getattr(context, "workspace", None), "root", None)
    return str(root) if root is not None else os.getcwd()


async def check_bash_permissions(args: dict[str, Any], context: ToolContext) -> PermissionResult:
    """`BashTool.checkPermissions` -> `bashToolHasPermission`."""
    return bash_tool_has_permission(
        str(args.get("command") or ""),
        extract_permission_context(context),
        permission_cwd(context),
    )


def build_bash_tool(vars: Mapping[str, Any] | None = None) -> BuiltTool:
    async def call_bash(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        command = str(input_args.get("command") or "")
        ws = getattr(context, "workspace", None)
        if ws is None:
            return ToolResult(content="No workspace available", is_error=True)

        timeout_ms = input_args.get("timeout") or get_default_bash_timeout_ms()
        try:
            res = ws.execute(command, timeout=timeout_ms / 1000)
        except Exception as e:
            return ToolResult(content=f"Execution error: {e}", is_error=True)

        content = res.stdout
        if res.stderr:
            content += f"\nSTDERR:\n{res.stderr}"
        return ToolResult(
            content=content.strip() or "Command executed successfully (no output).",
            exit_code=res.exit_code,
            is_error=res.exit_code != 0,
        )

    prompt_text = BASH_PROMPT
    if vars:
        from app.loop.prompts.loader import render_prompt
        prompt_text = render_prompt(BASH_PROMPT, vars)

    return build_tool(
        name="Bash",
        prompt=prompt_text,
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
            },
            "required": ["command"],
        },
        description=lambda args: args.get("description") or "Run shell command",
        is_read_only=is_bash_read_only,
        is_concurrency_safe=is_bash_read_only,
        validate_input=validate_bash_input,
        check_permissions=check_bash_permissions,
        call=call_bash,
    )
