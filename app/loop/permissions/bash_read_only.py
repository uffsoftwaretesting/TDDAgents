"""
Whether a Bash command is read-only, and so may run without a permission rule.

Ported from claude-code v2.1.88 `src/tools/BashTool/readOnlyValidation.ts`, legacy
(no tree-sitter) path: `isCommandSafeViaFlagParsing`, `makeRegexForSafeCommand`,
`READONLY_COMMANDS`, `READONLY_COMMAND_REGEXES`, `containsUnquotedExpansion`,
`isCommandReadOnly`, `commandHasAnyGit`, `GIT_INTERNAL_PATTERNS`, `isGitInternalPath`,
`extractWritePathsFromSubcommand`, `commandWritesToGitInternalPaths`,
`checkReadOnlyConstraints`; and `src/utils/git.ts` -> `isCurrentDirectoryBareGitRepo`.
`BashTool.isReadOnly` is `is_bash_read_only`.

This replaces the Part C4 classifier (`capability.py`) for the Bash tool.

Divergences: no sandbox manager exists here, so the "git outside the original cwd while
sandboxed" gate never fires (upstream's `SandboxManager.isSandboxingEnabled()` false); the
tool-level predicate (`is_bash_read_only`) receives no context, so its bare-repo check
uses the process working directory where upstream uses `getCwd()` — the permission
check passes the workspace root instead. Regexes keep JavaScript semantics (`\\s` is the
JS whitespace class, `\\b` ASCII, `$` is `\\Z`).
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.loop.permissions.bash_commands import extract_output_redirections, split_command
from app.loop.permissions.bash_path_validation import COMMAND_OPERATION_TYPE, PATH_EXTRACTORS
from app.loop.permissions.bash_read_only_commands import (
    EXTERNAL_READONLY_COMMANDS,
    JS_WS,
    SAFE_TARGET_COMMANDS_FOR_XARGS,
    contains_vulnerable_unc_path,
    get_command_allowlist,
    validate_flags,
)
from app.loop.permissions.bash_security import bash_command_is_safe, try_parse_shell_command
from app.loop.permissions.types import PermissionBehavior, PermissionResult

_S = "[" + JS_WS + "]"
_NS = "[^" + JS_WS + "]"
_DOT = "[^\n\r  ]"
_JS_TRIM = "\t\n\x0b\x0c\r              " \
    "    　﻿"


def _keep_var(name: str) -> str:
    return "$" + name


# ── flag parsing ─────────────────────────────────────────────────────────────

def is_command_safe_via_flag_parsing(command: str) -> bool:
    """`isCommandSafeViaFlagParsing`: an allowlisted command whose every flag is known safe."""
    parsed = try_parse_shell_command(command, _keep_var)
    if not parsed.success:
        return False
    tokens: list[str] = []
    for token in parsed.tokens:
        if isinstance(token, str):
            tokens.append(token)
        elif token.get("op") == "glob":
            tokens.append(str(token["pattern"]))
        else:
            return False
    if not tokens:
        return False

    allowlist = get_command_allowlist()
    config = None
    command_tokens = 0
    for pattern, candidate in allowlist.items():
        words = pattern.split(" ")
        if len(tokens) >= len(words) and tokens[:len(words)] == words:
            config, command_tokens = candidate, len(words)
            break
    if config is None:
        return False

    if tokens[0] == "git" and tokens[1:2] == ["ls-remote"]:
        for token in tokens[2:]:
            if token and not token.startswith("-") and ("://" in token or "@" in token or ":" in token or "$" in token):
                return False

    for token in tokens[command_tokens:]:
        if not token:
            continue
        if "$" in token:
            return False
        if "{" in token and ("," in token or ".." in token):
            return False

    if not validate_flags(
        tokens,
        command_tokens,
        config,
        command_name=tokens[0],
        xargs_target_commands=SAFE_TARGET_COMMANDS_FOR_XARGS if tokens[0] == "xargs" else None,
    ):
        return False
    if config.regex is not None and not config.regex.search(command):
        return False
    if config.regex is None and "`" in command:
        return False
    if config.regex is None and tokens[0] in ("rg", "grep") and re.search("[\n\r]", command):
        return False
    return not (config.callback is not None and config.callback(command, tokens[command_tokens:]))


# ── regex allowlist ──────────────────────────────────────────────────────────

def make_regex_for_safe_command(command: str) -> re.Pattern[str]:
    """`makeRegexForSafeCommand`: the command, then no shell metacharacters at all."""
    return re.compile("^" + command + "(?:" + _S + r"|\Z)[^<>()$`|{}&;\n\r]*\Z")


READONLY_COMMANDS: tuple[str, ...] = (
    *EXTERNAL_READONLY_COMMANDS,
    "cal", "uptime",
    "cat", "head", "tail", "wc", "stat", "strings", "hexdump", "od", "nl",
    "id", "uname", "free", "df", "du", "locale", "groups", "nproc",
    "basename", "dirname", "realpath",
    "cut", "paste", "tr", "column", "tac", "rev", "fold", "expand", "unexpand", "fmt", "comm", "cmp", "numfmt",
    "readlink",
    "diff",
    "true", "false",
    "sleep", "which", "type", "expr", "test", "getconf", "seq", "tsort", "pr",
)

_A = re.ASCII  # JS `\b` and `\w` are ASCII

READONLY_COMMAND_REGEXES: tuple[re.Pattern[str], ...] = (
    *(make_regex_for_safe_command(c) for c in READONLY_COMMANDS),
    re.compile(
        "^echo(?:" + _S + "+(?:'[^']*'|\"[^\"$<>\n\r]*\"|[^|;&`$(){}><#\\\\!\"'" + JS_WS + "]+))*"
        "(?:" + _S + "+2>&1)?" + _S + r"*\Z"
    ),
    re.compile(r"^claude -h\Z"),
    re.compile(r"^claude --help\Z"),
    re.compile(
        "^uniq(?:" + _S + "+(?:-[a-zA-Z]+|--[a-zA-Z-]+(?:=" + _NS + "+)?|-[fsw]" + _S + "+[0-9]+))*"
        "(?:" + _S + r"|\Z)" + _S + r"*\Z"
    ),
    re.compile(r"^pwd\Z"),
    re.compile(r"^whoami\Z"),
    re.compile(r"^node -v\Z"),
    re.compile(r"^node --version\Z"),
    re.compile(r"^python --version\Z"),
    re.compile(r"^python3 --version\Z"),
    re.compile("^history(?:" + _S + "+[0-9]+)?" + _S + r"*\Z"),
    re.compile(r"^alias\Z"),
    re.compile("^arch(?:" + _S + "+(?:--help|-h))?" + _S + r"*\Z"),
    re.compile(r"^ip addr\Z"),
    re.compile("^ifconfig(?:" + _S + "+[a-zA-Z][a-zA-Z0-9_-]*)?" + _S + r"*\Z"),
    re.compile(
        "^jq(?!" + _S + "+" + _DOT + r"*(?:-f\b|--from-file|--rawfile|--slurpfile|--run-tests|-L\b|--library-path"
        r"|\benv\b|\$ENV\b))"
        "(?:" + _S + "+(?:-[a-zA-Z]+|--[a-zA-Z-]+(?:=" + _NS + "+)?))*"
        "(?:" + _S + "+'[^'`]*'|" + _S + "+\"[^\"`]*\"|" + _S + "+[^-" + JS_WS + "'\"]" + _NS + "*)+"
        + _S + r"*\Z",
        _A,
    ),
    re.compile("^cd(?:" + _S + "+(?:'[^']*'|\"[^\"]*\"|[^" + JS_WS + ";|&`$(){}><#\\\\]+))?\\Z"),
    re.compile("^ls(?:" + _S + "+[^<>()$`|{}&;\n\r]*)?\\Z"),
    re.compile(
        "^find(?:" + _S + r"+(?:\\[()]|(?!-delete\b|-exec\b|-execdir\b|-ok\b|-okdir\b|-fprint0?\b|-fls\b|-fprintf\b)"
        "[^<>()$`|{}&;\n\r" + JS_WS + "]|" + _S + ")+)?\\Z",
        _A,
    ),
)


def contains_unquoted_expansion(command: str) -> bool:
    """`containsUnquotedExpansion`: an unquoted glob, or `$VAR` outside single quotes."""
    in_single = False
    in_double = False
    escaped = False
    for i, ch in enumerate(command):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single:
            continue
        if ch == "$" and i + 1 < len(command) and re.match(r"[A-Za-z_@*#?!$0-9-]", command[i + 1]):
            return True
        if in_double:
            continue
        if ch in "?*[]":
            return True
    return False


def is_command_read_only(command: str) -> bool:
    """`isCommandReadOnly`: flag-parsed allowlist first, then the regex allowlist."""
    test = command.strip(_JS_TRIM)
    if test.endswith(" 2>&1"):
        test = test[:-5].strip(_JS_TRIM)
    if contains_vulnerable_unc_path(test) or contains_unquoted_expansion(test):
        return False
    if is_command_safe_via_flag_parsing(test):
        return True
    for regex in READONLY_COMMAND_REGEXES:
        if regex.search(test):
            if "git" in test and (
                re.search(_S + "-c[" + JS_WS + "=]", test)
                or re.search(_S + "--exec-path[" + JS_WS + "=]", test)
                or re.search(_S + "--config-env[" + JS_WS + "=]", test)
            ):
                return False
            return True
    return False


# ── git hardening ────────────────────────────────────────────────────────────

def command_has_any_git(command: str) -> bool:
    """`commandHasAnyGit`."""
    from app.loop.permissions.bash_permissions import is_normalized_git_command

    return any(is_normalized_git_command(sub.strip(_JS_TRIM)) for sub in split_command(command))


GIT_INTERNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^HEAD\Z"),
    re.compile(r"^objects(?:/|\Z)"),
    re.compile(r"^refs(?:/|\Z)"),
    re.compile(r"^hooks(?:/|\Z)"),
)

#: Commands that delete or edit in place rather than create files at new paths.
NON_CREATING_WRITE_COMMANDS = frozenset({"rm", "rmdir", "sed"})


def is_git_internal_path(path: str) -> bool:
    normalized = re.sub(r"^\.?/", "", path)
    return any(p.search(normalized) for p in GIT_INTERNAL_PATTERNS)


def extract_write_paths_from_subcommand(subcommand: str) -> list[str]:
    """`extractWritePathsFromSubcommand`: paths mkdir/touch/cp/mv would create."""
    parsed = try_parse_shell_command(subcommand, _keep_var)
    if not parsed.success:
        return []
    tokens = [t for t in parsed.tokens if isinstance(t, str)]
    if not tokens or not tokens[0]:
        return []
    base = tokens[0]
    operation = COMMAND_OPERATION_TYPE.get(base)
    if operation not in ("write", "create") or base in NON_CREATING_WRITE_COMMANDS:
        return []
    return PATH_EXTRACTORS[base](tokens[1:])


def command_writes_to_git_internal_paths(command: str) -> bool:
    """`commandWritesToGitInternalPaths`: creates HEAD/objects/refs/hooks, by command or `>`."""
    for sub in split_command(command):
        trimmed = sub.strip(_JS_TRIM)
        if any(is_git_internal_path(p) for p in extract_write_paths_from_subcommand(trimmed)):
            return True
        if any(is_git_internal_path(r.target) for r in extract_output_redirections(trimmed).redirections):
            return True
    return False


def is_current_directory_bare_git_repo(cwd: str) -> bool:
    """`isCurrentDirectoryBareGitRepo`: no valid `.git/HEAD`, but HEAD/objects/refs in cwd."""
    git_path = os.path.join(cwd, ".git")
    if os.path.isfile(git_path):
        return False
    if os.path.isdir(git_path) and os.path.isfile(os.path.join(git_path, "HEAD")):
        return False
    return (
        os.path.isfile(os.path.join(cwd, "HEAD"))
        or os.path.isdir(os.path.join(cwd, "objects"))
        or os.path.isdir(os.path.join(cwd, "refs"))
    )


# ── checkReadOnlyConstraints ─────────────────────────────────────────────────

def _passthrough(message: str) -> PermissionResult:
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message=message)


NOT_READ_ONLY = "Command is not read-only, requires further permission checks"


def check_read_only_constraints(
    command: str, compound_command_has_cd: bool, cwd: str | None = None
) -> PermissionResult:
    """`checkReadOnlyConstraints`: 'allow' only when every subcommand is read-only."""
    if not try_parse_shell_command(command, _keep_var).success:
        return _passthrough("Command cannot be parsed, requires further permission checks")
    if bash_command_is_safe(command).behavior != PermissionBehavior.PASSTHROUGH:
        return _passthrough(NOT_READ_ONLY)
    if contains_vulnerable_unc_path(command):
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message="Command contains Windows UNC path that could be vulnerable to WebDAV attacks",
        )
    has_git = command_has_any_git(command)
    if compound_command_has_cd and has_git:
        return _passthrough("Compound commands with cd and git require permission checks for enhanced security")
    if has_git and is_current_directory_bare_git_repo(cwd if cwd is not None else os.getcwd()):
        return _passthrough(
            "Git commands in directories with bare repository structure require permission checks for enhanced "
            "security"
        )
    if has_git and command_writes_to_git_internal_paths(command):
        return _passthrough(
            "Compound commands that create git internal files and run git require permission checks for enhanced "
            "security"
        )
    all_read_only = all(
        bash_command_is_safe(sub).behavior == PermissionBehavior.PASSTHROUGH and is_command_read_only(sub)
        for sub in split_command(command)
    )
    if all_read_only:
        return PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input={"command": command})
    return _passthrough(NOT_READ_ONLY)


def is_bash_read_only(args: dict[str, Any], cwd: str | None = None) -> bool:
    """`BashTool.isReadOnly`."""
    from app.loop.permissions.bash_permissions import command_has_any_cd

    command = str(args.get("command") or "")
    return check_read_only_constraints(command, command_has_any_cd(command), cwd).behavior == PermissionBehavior.ALLOW


__all__ = [
    "READONLY_COMMANDS",
    "READONLY_COMMAND_REGEXES",
    "check_read_only_constraints",
    "command_has_any_git",
    "command_writes_to_git_internal_paths",
    "contains_unquoted_expansion",
    "extract_write_paths_from_subcommand",
    "is_bash_read_only",
    "is_command_read_only",
    "is_command_safe_via_flag_parsing",
    "is_current_directory_bare_git_repo",
    "is_git_internal_path",
    "make_regex_for_safe_command",
]
