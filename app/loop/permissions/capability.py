"""
Per-input capability determination (Part C4).

Determines whether a tool invocation is read-only based on its input arguments:
- `Bash("ls")` is a read.
- `Bash("pip install")` is a write / state mutation.
- `ReadFile(...)` is a read.
- `WriteFile(...)` is a write.

Ported from:
- `reference/claude-code/src/tools/BashTool/readOnlyValidation.ts`
- `reference/claude-code/src/utils/shell/readOnlyCommandValidation.ts`
"""

from __future__ import annotations

import re
import shlex
from typing import Any

# Pure read-only base commands
READ_ONLY_BASE_COMMANDS: frozenset[str] = frozenset({
    # File listing & inspection
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "stat",
    "strings",
    "file",
    "diff",
    "cmp",
    # Text processing
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "cut",
    "paste",
    "tr",
    "sort",
    "uniq",
    "jq",
    "awk",
    # System & environment info
    "pwd",
    "which",
    "whereis",
    "type",
    "uname",
    "id",
    "whoami",
    "uptime",
    "df",
    "du",
    "free",
    "locale",
    "nproc",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "echo",
    "printf",
    "true",
    "false",
    "test",
    "expr",
    "seq",
    "sleep",
})

# Git subcommands that are strictly read-only
GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset({
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "tag",
    "rev-parse",
    "ls-files",
    "blame",
    "remote",
    "describe",
    "version",
})

# Wrapper commands that simply invoke another command
COMMAND_WRAPPERS: frozenset[str] = frozenset({
    "time",
    "timeout",
    "nohup",
    "env",
    "command",
    "builtin",
})

# Pattern detecting output redirections ('>', '>>', '>|') outside quotes
OUTPUT_REDIRECTION_PATTERN = re.compile(
    r"""(?<!\d)(?:>>|>\||>)(?![^"']*"(?:[^"']*"[^"']*")*[^"']*$)(?![^']*'(?:[^']*'[^']*')*[^']*$)"""
)

# Pattern detecting duration arguments for commands like timeout (e.g. 5, 10s, 1.5m)
DURATION_PATTERN = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def has_output_redirection(command: str) -> bool:
    """
    Check if a shell command contains file output redirection ('>' or '>>').
    Writing output to a file is a mutating operation.
    """
    # Simple check first
    if ">" not in command:
        return False

    # Strip quoted strings to inspect unquoted operators
    unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "", command)
    return ">" in unquoted


def split_shell_commands(command: str) -> list[str]:
    """
    Split a compound shell command into individual commands by ';', '&&', '||', or '|'.
    Respects single and double quotes.
    """
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape = False

    i = 0
    while i < len(command):
        c = command[i]

        if escape:
            current.append(c)
            escape = False
            i += 1
            continue

        if c == "\\":
            escape = True
            current.append(c)
            i += 1
            continue

        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue

        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue

        if not in_single and not in_double:
            # Check delimiters
            if c == ";":
                parts.append("".join(current).strip())
                current = []
                i += 1
                continue
            elif c == "|" and i + 1 < len(command) and command[i + 1] == "|":
                parts.append("".join(current).strip())
                current = []
                i += 2
                continue
            elif c == "&" and i + 1 < len(command) and command[i + 1] == "&":
                parts.append("".join(current).strip())
                current = []
                i += 2
                continue
            elif c == "|":
                parts.append("".join(current).strip())
                current = []
                i += 1
                continue

        current.append(c)
        i += 1

    if current:
        parts.append("".join(current).strip())

    return [p for p in parts if p]


def is_single_subcommand_read_only(cmd_str: str) -> bool:
    """
    Validate a single subcommand (no operators/pipes).
    """
    stripped = cmd_str.strip()
    if not stripped:
        return True

    # Redirections make any command a write
    if has_output_redirection(stripped):
        return False

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        # Malformed quotes -> fail closed
        return False

    if not tokens:
        return True

    # Strip command wrappers (e.g. 'env', 'timeout') and environment variable assignments
    while tokens:
        if tokens[0] in COMMAND_WRAPPERS:
            wrapper = tokens.pop(0)
            if wrapper == "timeout":
                while tokens and tokens[0].startswith("-"):
                    opt = tokens.pop(0)
                    if opt in ("-k", "--kill-after", "-s", "--signal") and tokens:
                        tokens.pop(0)
                if tokens and DURATION_PATTERN.match(tokens[0]):
                    tokens.pop(0)
            elif wrapper == "env":
                while tokens and tokens[0].startswith("-"):
                    opt = tokens.pop(0)
                    if opt in ("-u", "--unset") and tokens:
                        tokens.pop(0)
            else:
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
            continue

        if "=" in tokens[0] and not tokens[0].startswith("-"):
            tokens.pop(0)
            continue

        break

    if not tokens:
        return True

    head = tokens[0]

    # Handle python / python3 / node checking versions
    if head in ("python", "python3", "node", "ruby", "perl"):
        if len(tokens) > 1 and tokens[1] in ("-v", "--version", "-V"):
            return True
        return False

    # Git command inspection
    if head == "git":
        if len(tokens) == 1:
            return True  # 'git' alone just prints help
        subcmd = tokens[1]
        # Ignore global git flags like -C <path>
        idx = 1
        while idx < len(tokens) and tokens[idx].startswith("-"):
            if tokens[idx] in ("-C", "-c") and idx + 1 < len(tokens):
                idx += 2
            else:
                idx += 1
        if idx < len(tokens):
            subcmd = tokens[idx]
            return subcmd in GIT_READ_ONLY_SUBCOMMANDS
        return True

    # sed inspection: fail if -i or --in-place is passed
    if head == "sed":
        if any(tok.startswith("-i") or tok.startswith("--in-place") for tok in tokens):
            return False
        return True

    # Standard read-only commands
    if head in READ_ONLY_BASE_COMMANDS:
        return True

    # Any unknown command (e.g. rm, pip, npm, apt, touch, etc.) is NOT read-only
    return False


def is_bash_command_read_only(command: str) -> bool:
    """
    Determine if an entire bash command string is strictly read-only (Part C4).

    Every chained subcommand must be read-only, and no file redirections are allowed.
    """
    if not command.strip():
        return True

    subcommands = split_shell_commands(command)
    if not subcommands:
        return True

    return all(is_single_subcommand_read_only(subcmd) for subcmd in subcommands)


def bash_is_read_only(args: dict[str, Any]) -> bool:
    """
    Predicate suitable for build_tool(is_read_only=bash_is_read_only).
    """
    cmd = str(args.get("command") or args.get("cmd") or "")
    return is_bash_command_read_only(cmd)
