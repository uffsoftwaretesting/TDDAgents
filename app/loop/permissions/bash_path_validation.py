"""
Per-command path validation for the Bash tool: which files and directories a command
like `rm`, `cp`, `cat` or `find` touches, and whether each lies where it may.

Ported from claude-code v2.1.88, legacy (no tree-sitter) path:

* `src/tools/BashTool/pathValidation.ts` -> `PathCommand`, `checkDangerousRemovalPaths`,
  `filterOutFlags`, `parsePatternCommand`, `PATH_EXTRACTORS`, `ACTION_VERBS`,
  `COMMAND_OPERATION_TYPE`, `COMMAND_VALIDATOR`, `validateCommandPaths`,
  `createPathChecker`, `parseCommandArguments`, `validateSinglePathCommand`.
* `src/utils/permissions/pathValidation.ts` -> `formatDirectoryList`,
  `getGlobBaseDirectory`, `expandTilde`, `validateGlobPattern`, `isDangerousRemovalPath`,
  `validatePath`; `src/utils/path.ts` -> `containsPathTraversal`.

`validatePath`'s pre-checks (quotes, tilde variants, UNC, shell expansion, globs) are
upstream's. The final containment and mode decision is this repository's Part C5
`is_path_allowed`, mapped back to upstream's shape: allowed, or a `rule`/`safetyCheck`
reason that is carried through, or no reason (the standard "was blocked" message).

Divergences: paths are resolved with `os.path.normpath` (Node's `path.resolve`), without
upstream's symlink canonicalisation (`safeResolvePath`); the AST-only
`validateSinglePathCommandArgv`/`stripWrappersFromArgv` are not needed on this path; and
permission-update suggestions are not produced (they feed an approval UI this loop does
not have). Messages name TDDAgents where upstream names Claude Code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from app.loop.permissions.bash_read_only_commands import contains_vulnerable_unc_path
from app.loop.permissions.bash_security import try_parse_shell_command
from app.loop.permissions.filesystem import is_path_allowed
from app.loop.permissions.types import PermissionBehavior, PermissionResult

if TYPE_CHECKING:
    from app.loop.permissions.types import ToolPermissionContext

FileOperationType = Literal["read", "write", "create"]

#: `MAX_DIRS_TO_LIST` — how many working directories a message names (display only).
MAX_DIRS_TO_LIST = 5
GLOB_PATTERN_REGEX = re.compile(r"[*?[\]{}]")


# ── helpers (utils/permissions/pathValidation.ts, utils/path.ts) ─────────────

def format_directory_list(directories: list[str]) -> str:
    """`formatDirectoryList`."""
    if len(directories) <= MAX_DIRS_TO_LIST:
        return ", ".join(f"'{d}'" for d in directories)
    first = ", ".join(f"'{d}'" for d in directories[:MAX_DIRS_TO_LIST])
    return f"{first}, and {len(directories) - MAX_DIRS_TO_LIST} more"


def home_dir() -> str:
    return str(Path.home())


def expand_tilde(path: str) -> str:
    """`expandTilde`: `~` and `~/...` only; `~user` is never expanded."""
    if path == "~" or path.startswith("~/"):
        return home_dir() + path[1:]
    return path


def contains_path_traversal(path: str) -> bool:
    """`containsPathTraversal`."""
    return re.search(r"(?:^|[\\/])\.\.(?:[\\/]|\Z)", path) is not None


def get_glob_base_directory(path: str) -> str:
    """`getGlobBaseDirectory`: the directory before the first glob character."""
    m = GLOB_PATTERN_REGEX.search(path)
    if m is None:
        return path
    before = path[:m.start()]
    last_sep = before.rfind("/")
    if last_sep == -1:
        return "."
    return before[:last_sep] or "/"


def _resolve(path: str, cwd: str) -> str:
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def _is_path_allowed(
    resolved: str, context: ToolPermissionContext, cwd: str, operation: FileOperationType
) -> tuple[bool, dict[str, object] | None]:
    verdict = is_path_allowed(resolved, context, cwd, "read" if operation == "read" else "write")
    if verdict.behavior == PermissionBehavior.ALLOW:
        return True, None
    reason = verdict.decision_reason or {}
    if reason.get("type") in ("rule", "safetyCheck"):
        return False, reason
    return False, None


def validate_path(
    path: str, cwd: str, context: ToolPermissionContext, operation: FileOperationType
) -> tuple[bool, str, dict[str, object] | None]:
    """`validatePath` -> (allowed, resolved_path, decision_reason)."""
    clean = expand_tilde(re.sub(r"""^['"]|['"]$""", "", path))
    if contains_vulnerable_unc_path(clean):
        return False, clean, {"type": "other", "reason": "UNC network paths require manual approval"}
    if clean.startswith("~"):
        return False, clean, {
            "type": "other",
            "reason": "Tilde expansion variants (~user, ~+, ~-) in paths require manual approval",
        }
    if "$" in clean or "%" in clean or clean.startswith("="):
        return False, clean, {"type": "other", "reason": "Shell expansion syntax in paths requires manual approval"}
    if GLOB_PATTERN_REGEX.search(clean):
        if operation in ("write", "create"):
            return False, clean, {
                "type": "other",
                "reason": "Glob patterns are not allowed in write operations. Please specify an exact file path.",
            }
        base = clean if contains_path_traversal(clean) else get_glob_base_directory(clean)
        resolved = _resolve(base, cwd)
        allowed, reason = _is_path_allowed(resolved, context, cwd, operation)
        return allowed, resolved, reason
    resolved = _resolve(clean, cwd)
    allowed, reason = _is_path_allowed(resolved, context, cwd, operation)
    return allowed, resolved, reason


def is_dangerous_removal_path(resolved_path: str) -> bool:
    """`isDangerousRemovalPath`: `*`, `.../*`, `/`, a drive root, `~`, or a child of `/`."""
    forward = re.sub(r"[\\/]+", "/", resolved_path)
    if forward == "*" or forward.endswith("/*"):
        return True
    normalized = forward if forward == "/" else re.sub(r"/\Z", "", forward)
    if normalized == "/":
        return True
    if re.fullmatch(r"[A-Za-z]:/?", normalized):
        return True
    if normalized == re.sub(r"[\\/]+", "/", home_dir()):
        return True
    if os.path.dirname(normalized) == "/":
        return True
    return re.fullmatch(r"[A-Za-z]:/[^/]+", normalized) is not None


# ── extractors (tools/BashTool/pathValidation.ts) ────────────────────────────

def filter_out_flags(args: list[str]) -> list[str]:
    """`filterOutFlags`: positional args, honouring `--` (`rm -- -/../x` is a path)."""
    result: list[str] = []
    after_dash_dash = False
    for arg in args:
        if after_dash_dash:
            result.append(arg)
        elif arg == "--":
            after_dash_dash = True
        elif not arg.startswith("-"):
            result.append(arg)
    return result


def parse_pattern_command(
    args: list[str], flags_with_args: frozenset[str], defaults: list[str] | None = None
) -> list[str]:
    """`parsePatternCommand`: grep/rg style — the first operand is the pattern."""
    paths: list[str] = []
    pattern_found = False
    after_dash_dash = False
    i = 0
    while i < len(args):
        arg = args[i]
        if not after_dash_dash and arg == "--":
            after_dash_dash = True
        elif not after_dash_dash and arg.startswith("-"):
            flag = arg.split("=")[0]
            if flag in ("-e", "--regexp", "-f", "--file"):
                pattern_found = True
            if flag and flag in flags_with_args and "=" not in arg:
                i += 1
        elif not pattern_found:
            pattern_found = True
        else:
            paths.append(arg)
        i += 1
    return paths if paths else list(defaults or [])


def _extract_cd(args: list[str]) -> list[str]:
    return [home_dir()] if not args else [" ".join(args)]


def _extract_ls(args: list[str]) -> list[str]:
    return filter_out_flags(args) or ["."]


_FIND_PATH_FLAGS = frozenset({
    "-newer", "-anewer", "-cnewer", "-mnewer", "-samefile", "-path", "-wholename", "-ilname", "-lname",
    "-ipath", "-iwholename",
})


def _extract_find(args: list[str]) -> list[str]:
    paths: list[str] = []
    found_non_global = False
    after_dash_dash = False
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if after_dash_dash:
            paths.append(arg)
        elif arg == "--":
            after_dash_dash = True
        elif arg.startswith("-"):
            if arg not in ("-H", "-L", "-P"):
                found_non_global = True
                if arg in _FIND_PATH_FLAGS or re.fullmatch("-newer[acmBt][acmtB]", arg):
                    nxt = args[i + 1] if i + 1 < len(args) else ""
                    if nxt:
                        paths.append(nxt)
                        i += 1
        elif not found_non_global:
            paths.append(arg)
        i += 1
    return paths or ["."]


def _extract_tr(args: list[str]) -> list[str]:
    has_delete = any(a in ("-d", "--delete") or (a.startswith("-") and "d" in a) for a in args)
    return filter_out_flags(args)[1 if has_delete else 2:]


_GREP_FLAGS_WITH_ARGS = frozenset({
    "-e", "--regexp", "-f", "--file", "--exclude", "--include", "--exclude-dir", "--include-dir", "-m",
    "--max-count", "-A", "--after-context", "-B", "--before-context", "-C", "--context",
})
_RG_FLAGS_WITH_ARGS = frozenset({
    "-e", "--regexp", "-f", "--file", "-t", "--type", "-T", "--type-not", "-g", "--glob", "-m", "--max-count",
    "--max-depth", "-r", "--replace", "-A", "--after-context", "-B", "--before-context", "-C", "--context",
})


def _extract_grep(args: list[str]) -> list[str]:
    paths = parse_pattern_command(args, _GREP_FLAGS_WITH_ARGS)
    if not paths and any(a in ("-r", "-R", "--recursive") for a in args):
        return ["."]
    return paths


def _extract_rg(args: list[str]) -> list[str]:
    return parse_pattern_command(args, _RG_FLAGS_WITH_ARGS, ["."])


def _extract_sed(args: list[str]) -> list[str]:
    paths: list[str] = []
    skip_next = False
    script_found = False
    after_dash_dash = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if not arg:
            continue
        if not after_dash_dash and arg == "--":
            after_dash_dash = True
            continue
        if not after_dash_dash and arg.startswith("-"):
            if arg in ("-f", "--file"):
                script = args[i + 1] if i + 1 < len(args) else ""
                if script:
                    paths.append(script)
                    skip_next = True
                script_found = True
            elif arg in ("-e", "--expression"):
                skip_next = True
                script_found = True
            elif "e" in arg or "f" in arg:
                script_found = True
            continue
        if not script_found:
            script_found = True
            continue
        paths.append(arg)
    return paths


_JQ_FLAGS_WITH_ARGS = frozenset({
    "-e", "--expression", "-f", "--from-file", "--arg", "--argjson", "--slurpfile", "--rawfile", "--args",
    "--jsonargs", "-L", "--library-path", "--indent", "--tab",
})


def _extract_jq(args: list[str]) -> list[str]:
    paths: list[str] = []
    filter_found = False
    after_dash_dash = False
    i = 0
    while i < len(args):
        arg = args[i]
        if not after_dash_dash and arg == "--":
            after_dash_dash = True
        elif not after_dash_dash and arg.startswith("-"):
            flag = arg.split("=")[0]
            if flag in ("-e", "--expression"):
                filter_found = True
            if flag and flag in _JQ_FLAGS_WITH_ARGS and "=" not in arg:
                i += 1
        elif not filter_found:
            filter_found = True
        else:
            paths.append(arg)
        i += 1
    return paths


def _extract_git(args: list[str]) -> list[str]:
    """Only `git diff --no-index` reaches outside the repository."""
    if args and args[0] == "diff" and "--no-index" in args:
        return filter_out_flags(args[1:])[:2]
    return []


PATH_EXTRACTORS: dict[str, Callable[[list[str]], list[str]]] = {
    "cd": _extract_cd,
    "ls": _extract_ls,
    "find": _extract_find,
    **{name: filter_out_flags for name in (
        "mkdir", "touch", "rm", "rmdir", "mv", "cp", "cat", "head", "tail", "sort", "uniq", "wc", "cut", "paste",
        "column", "file", "stat", "diff", "awk", "strings", "hexdump", "od", "base64", "nl", "sha256sum",
        "sha1sum", "md5sum",
    )},
    "tr": _extract_tr,
    "grep": _extract_grep,
    "rg": _extract_rg,
    "sed": _extract_sed,
    "jq": _extract_jq,
    "git": _extract_git,
}

ACTION_VERBS: dict[str, str] = {
    "cd": "change directories to", "ls": "list files in", "find": "search files in",
    "mkdir": "create directories in", "touch": "create or modify files in", "rm": "remove files from",
    "rmdir": "remove directories from", "mv": "move files to/from", "cp": "copy files to/from",
    "cat": "concatenate files from", "head": "read the beginning of files from",
    "tail": "read the end of files from", "sort": "sort contents of files from",
    "uniq": "filter duplicate lines from files in", "wc": "count lines/words/bytes in files from",
    "cut": "extract columns from files in", "paste": "merge files from", "column": "format files from",
    "tr": "transform text from files in", "file": "examine file types in", "stat": "read file stats from",
    "diff": "compare files from", "awk": "process text from files in", "strings": "extract strings from files in",
    "hexdump": "display hex dump of files from", "od": "display octal dump of files from",
    "base64": "encode/decode files from", "nl": "number lines in files from",
    "grep": "search for patterns in files from", "rg": "search for patterns in files from", "sed": "edit files in",
    "git": "access files with git from", "jq": "process JSON from files in",
    "sha256sum": "compute SHA-256 checksums for files in", "sha1sum": "compute SHA-1 checksums for files in",
    "md5sum": "compute MD5 checksums for files in",
}

COMMAND_OPERATION_TYPE: dict[str, FileOperationType] = {
    **{name: "read" for name in PATH_EXTRACTORS},
    "mkdir": "create", "touch": "create", "rm": "write", "rmdir": "write", "mv": "write", "cp": "write",
    "sed": "write",
}

#: `COMMAND_VALIDATOR`: mv/cp flags such as `--target-directory=PATH` evade extraction.
COMMAND_VALIDATOR: dict[str, Callable[[list[str]], bool]] = {
    "mv": lambda args: not any(a.startswith("-") for a in args),
    "cp": lambda args: not any(a.startswith("-") for a in args),
}

SUPPORTED_PATH_COMMANDS: tuple[str, ...] = tuple(PATH_EXTRACTORS)


# ── validation ───────────────────────────────────────────────────────────────

def _ask(message: str, reason: str) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.ASK, message=message, decision_reason={"type": "other", "reason": reason}
    )


def check_dangerous_removal_paths(command: str, args: list[str], cwd: str) -> PermissionResult:
    """`checkDangerousRemovalPaths`: rm/rmdir on `/`, `~`, `/usr`, `*`... always asks."""
    for path in PATH_EXTRACTORS[command](args):
        clean = expand_tilde(re.sub(r"""^['"]|['"]$""", "", path))
        absolute = clean if os.path.isabs(clean) else os.path.normpath(os.path.join(cwd, clean))
        if is_dangerous_removal_path(absolute):
            return _ask(
                f"Dangerous {command} operation detected: '{absolute}'\n\nThis command would remove a critical "
                "system directory. This requires explicit approval and cannot be auto-allowed by permission rules.",
                f"Dangerous {command} operation on critical path: {absolute}",
            )
    return PermissionResult(
        behavior=PermissionBehavior.PASSTHROUGH, message=f"No dangerous removals detected for {command} command"
    )


def validate_command_paths(
    command: str,
    args: list[str],
    cwd: str,
    context: ToolPermissionContext,
    compound_command_has_cd: bool = False,
    operation_override: FileOperationType | None = None,
) -> PermissionResult:
    """`validateCommandPaths`."""
    paths = PATH_EXTRACTORS[command](args)
    operation = operation_override or COMMAND_OPERATION_TYPE[command]

    validator = COMMAND_VALIDATOR.get(command)
    if validator is not None and not validator(args):
        return _ask(
            f"{command} with flags requires manual approval to ensure path safety. For security, TDDAgents "
            f"cannot automatically validate {command} commands that use flags, as some flags like "
            "--target-directory=PATH can bypass path validation.",
            f"{command} command with flags requires manual approval",
        )
    if compound_command_has_cd and operation != "read":
        return _ask(
            "Commands that change directories and perform write operations require explicit approval to ensure "
            "paths are evaluated correctly. For security, TDDAgents cannot automatically determine the final "
            "working directory when 'cd' is used in compound commands.",
            "Compound command contains cd with write operation - manual approval required to prevent path "
            "resolution bypass",
        )
    for path in paths:
        allowed, resolved, reason = validate_path(path, cwd, context, operation)
        if allowed:
            continue
        reason_type = reason.get("type") if reason else None
        if reason is not None and reason_type in ("other", "safetyCheck"):
            message = str(reason.get("reason"))
        else:
            dirs = format_directory_list([cwd, *context.additional_working_directories])
            message = (
                f"{command} in '{resolved}' was blocked. For security, TDDAgents may only "
                f"{ACTION_VERBS[command]} the allowed working directories for this session: {dirs}."
            )
        behavior = PermissionBehavior.DENY if reason_type == "rule" else PermissionBehavior.ASK
        return PermissionResult(behavior=behavior, message=message, decision_reason=reason)
    return PermissionResult(
        behavior=PermissionBehavior.PASSTHROUGH, message=f"Path validation passed for {command} command"
    )


def check_command_paths(
    command: str,
    args: list[str],
    cwd: str,
    context: ToolPermissionContext,
    compound_command_has_cd: bool = False,
    operation_override: FileOperationType | None = None,
) -> PermissionResult:
    """`createPathChecker(command, override)(...)`: deny rules first, then dangerous removals."""
    result = validate_command_paths(command, args, cwd, context, compound_command_has_cd, operation_override)
    if result.behavior == PermissionBehavior.DENY:
        return result
    if command in ("rm", "rmdir"):
        dangerous = check_dangerous_removal_paths(command, args, cwd)
        if dangerous.behavior != PermissionBehavior.PASSTHROUGH:
            return dangerous
    return result


def parse_command_arguments(cmd: str) -> list[str]:
    """`parseCommandArguments`: string tokens and glob patterns; [] if unparseable."""
    parsed = try_parse_shell_command(cmd, lambda name: "$" + name)
    if not parsed.success:
        return []
    out: list[str] = []
    for token in parsed.tokens:
        if isinstance(token, str):
            out.append(token)
        elif token.get("op") == "glob" and "pattern" in token:
            out.append(str(token["pattern"]))
    return out


def validate_single_path_command(
    cmd: str, cwd: str, context: ToolPermissionContext, compound_command_has_cd: bool = False
) -> PermissionResult:
    """`validateSinglePathCommand`: wrappers stripped, then the command's own path checker."""
    from app.loop.permissions.bash_permissions import strip_safe_wrappers
    from app.loop.permissions.bash_sed_validation import sed_command_is_allowed_by_allowlist

    stripped = strip_safe_wrappers(cmd)
    args = parse_command_arguments(stripped)
    if not args:
        return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="Empty command - no paths to validate")
    base, rest = args[0], args[1:]
    if not base or base not in SUPPORTED_PATH_COMMANDS:
        return PermissionResult(
            behavior=PermissionBehavior.PASSTHROUGH, message=f"Command '{base}' is not a path-restricted command"
        )
    override: FileOperationType | None = (
        "read" if base == "sed" and sed_command_is_allowed_by_allowlist(stripped) else None
    )
    return check_command_paths(base, rest, cwd, context, compound_command_has_cd, override)


__all__ = [
    "ACTION_VERBS",
    "COMMAND_OPERATION_TYPE",
    "PATH_EXTRACTORS",
    "SUPPORTED_PATH_COMMANDS",
    "check_command_paths",
    "check_dangerous_removal_paths",
    "contains_path_traversal",
    "expand_tilde",
    "filter_out_flags",
    "format_directory_list",
    "get_glob_base_directory",
    "is_dangerous_removal_path",
    "parse_command_arguments",
    "parse_pattern_command",
    "validate_command_paths",
    "validate_path",
    "validate_single_path_command",
]
