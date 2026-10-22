"""
Filesystem permissions: path rules, the working-directory boundary, dangerous paths, and
the write check of `WriteFile` / `Edit`.

Ported from claude-code v2.1.88:

* `src/utils/permissions/filesystem.ts` -> `DANGEROUS_FILES`, `DANGEROUS_DIRECTORIES`,
  `normalizeCaseForComparison`, `isDangerousFilePathToAutoEdit`,
  `hasSuspiciousWindowsPathPattern`, `checkPathSafetyForAutoEdit`, `allWorkingDirectories`,
  `pathInAllowedWorkingPath`, `pathInWorkingPath`, `relativePath`, `rootPathForSource`,
  `patternWithRoot`, `getPatternsByRoot`, `matchingRuleForInput`,
  `checkWritePermissionForTool`
* `src/utils/permissions/pathValidation.ts` -> `isPathAllowed`
* `src/utils/fsOperations.ts` -> `getPathsForPermissionCheck` (original + resolved path)

**Path rules are gitignore patterns on the `Edit`/`Read` tool names**, matched with the
pinned `pathspec` (upstream uses the `ignore` package; both implement gitignore). An
`Edit(src/**)` rule governs every tool that edits files — `WriteFile`, `Edit`, and the
redirect targets of `Bash` — exactly as upstream maps all of them onto `FILE_EDIT_TOOL_NAME`.
Anchoring follows `patternWithRoot`: `//x` is filesystem-absolute, `~/x` is home, `/x` is
the settings source's root, anything else floats under the working directory.

**The working directory is the workspace root.** Upstream's `getOriginalCwd()` is where
the agent works; here that is the session workspace, so project, local and policy patterns
anchor there (`ToolPermissionContext.settings_roots` carries the exceptions, user and flag).

**TDDAgents' config directory is protected too.** `.tddagents` and `.tddagents.json` join
upstream's dangerous lists (the product rename of `.claude` / `.claude.json`, which stay,
since a workspace may also be opened with Claude Code). Writing there could plant settings
or memory the next run would load.

Not ported, because no equivalent exists in this loop: the editable internal paths (plan
files, scratchpad, step 1.5), the `.claude/**` session-allow carve-out (step 1.6, which
serves an approval dialog), the sandbox write allowlist (step 3.7), permission-update
suggestions, the Windows-only ADS check, and the full symlink-chain walk — the resolved
path is checked, not each intermediate link.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Sequence

from pathspec.util import lookup_pattern

from app.loop.permissions.bash_read_only_commands import contains_vulnerable_unc_path
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

if TYPE_CHECKING:
    from app.loop.context import ToolContext

FileOperationType = Literal["read", "write", "create"]
ToolType = Literal["edit", "read"]
RuleBehavior = Literal["allow", "deny", "ask"]

#: `FILE_EDIT_TOOL_NAME` / `FILE_READ_TOOL_NAME`: the rule names path rules live under.
FILE_EDIT_TOOL_NAME = "Edit"
FILE_READ_TOOL_NAME = "Read"

DANGEROUS_FILES: tuple[str, ...] = (
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".claude.json",
    ".tddagents.json",
)

DANGEROUS_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".vscode",
    ".idea",
    ".claude",
    ".tddagents",
)

_GitIgnorePattern = lookup_pattern("gitignore")
_TRAILING_DOTS_OR_SPACES = re.compile(r"[.\s]+$")
_DOS_DEVICE = re.compile(r"\.(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)
_TRIPLE_DOTS = re.compile(r"(^|/|\\)\.{3,}(/|\\|$)")
_SHORT_NAME = re.compile(r"~\d")


@dataclass(frozen=True, slots=True)
class PathCheckResult:
    """`PathCheckResult` of `isPathAllowed`."""

    allowed: bool
    decision_reason: dict[str, Any] | None = None


def normalize_case_for_comparison(path: str) -> str:
    """`normalizeCaseForComparison`: always lowercase, whatever the platform."""
    return path.lower()


def expand_path(path: str, cwd: str) -> str:
    """`expandPath`: `~` to home, relative to `cwd`, normalized."""
    if path == "~":
        path = os.path.expanduser("~")
    elif path.startswith("~/"):
        path = os.path.join(os.path.expanduser("~"), path[2:])
    return posixpath.normpath(path if posixpath.isabs(path) else posixpath.join(cwd, path))


def get_paths_for_permission_check(path: str) -> tuple[str, ...]:
    """
    `getPathsForPermissionCheck`, reduced: the path and, when different, its resolved form
    (symlinks followed, including in a missing file's ancestors). UNC paths are never
    touched, so no lookup can reach the network.
    """
    if path.startswith("//") or path.startswith("\\\\"):
        return (path,)
    resolved = os.path.realpath(path)
    return (path,) if resolved == path else (path, resolved)


def is_dangerous_file_path_to_auto_edit(path: str) -> bool:
    """`isDangerousFilePathToAutoEdit`: UNC paths, dangerous directories and files."""
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    segments = path.split("/")
    for index, segment in enumerate(segments):
        normalized = normalize_case_for_comparison(segment)
        if normalized not in DANGEROUS_DIRECTORIES:
            continue
        # `.claude/worktrees/` is structural, not user configuration.
        if (
            normalized == ".claude"
            and index + 1 < len(segments)
            and normalize_case_for_comparison(segments[index + 1]) == "worktrees"
        ):
            continue
        return True
    file_name = segments[-1]
    return normalize_case_for_comparison(file_name) in DANGEROUS_FILES


def has_suspicious_windows_path_pattern(path: str) -> bool:
    """
    `hasSuspiciousWindowsPathPattern`, without the ADS colon check (Windows/WSL only):
    8.3 short names, long-path prefixes, trailing dots or spaces, DOS device names,
    `...` components and UNC paths. Checked on every platform, as upstream does, because
    NTFS can be mounted anywhere.
    """
    if _SHORT_NAME.search(path):
        return True
    if path.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        return True
    if _TRAILING_DOTS_OR_SPACES.search(path):
        return True
    if _DOS_DEVICE.search(path):
        return True
    if _TRIPLE_DOTS.search(path):
        return True
    return contains_vulnerable_unc_path(path)


def check_path_safety_for_auto_edit(path: str, paths_to_check: Sequence[str]) -> tuple[bool, str, bool]:
    """
    `checkPathSafetyForAutoEdit`: `(safe, message, classifier_approvable)`, over the original
    and resolved paths. A suspicious Windows path needs a human (`classifierApprovable:
    false`); a sensitive file may be approved by the auto-mode classifier (`true`).
    """
    for candidate in paths_to_check:
        if has_suspicious_windows_path_pattern(candidate):
            return False, (
                f"The agent requested permissions to write to {path}, which contains a suspicious "
                "Windows path pattern that requires manual approval."
            ), False
    for candidate in paths_to_check:
        if is_dangerous_file_path_to_auto_edit(candidate):
            return False, f"The agent requested permissions to edit {path} which is a sensitive file.", True
    return True, "", False


# ── working directories ──────────────────────────────────────────────────────

def all_working_directories(context: ToolPermissionContext, cwd: str) -> tuple[str, ...]:
    """`allWorkingDirectories`: the working directory, then the additional ones."""
    return (cwd, *context.additional_working_directories)


def _strip_private(path: str) -> str:
    path = re.sub(r"^/private/var/", "/var/", path)
    return re.sub(r"^/private/tmp(/|$)", r"/tmp\1", path)


def path_in_working_path(path: str, working_path: str) -> bool:
    """`pathInWorkingPath`: case-insensitive containment, macOS `/private` folded."""
    absolute = normalize_case_for_comparison(_strip_private(posixpath.normpath(path)))
    working = normalize_case_for_comparison(_strip_private(posixpath.normpath(working_path)))
    relative = posixpath.relpath(absolute, working)
    if relative == ".":
        return True
    return relative != ".." and not relative.startswith("../") and not posixpath.isabs(relative)


def path_in_allowed_working_path(
    path: str,
    context: ToolPermissionContext,
    cwd: str,
    paths_to_check: Sequence[str] | None = None,
) -> bool:
    """`pathInAllowedWorkingPath`: *every* checked form of the path is inside some working dir."""
    candidates = paths_to_check if paths_to_check is not None else get_paths_for_permission_check(path)
    working_paths = [
        resolved
        for working in all_working_directories(context, cwd)
        for resolved in get_paths_for_permission_check(working)
    ]
    return all(any(path_in_working_path(candidate, wp) for wp in working_paths) for candidate in candidates)


# ── path rules ───────────────────────────────────────────────────────────────

def root_path_for_source(source: PermissionRuleSource, context: ToolPermissionContext, cwd: str) -> str:
    """`rootPathForSource`: the settings root for user/flag rules, else the working dir."""
    return context.settings_roots.get(source, cwd)


def pattern_with_root(
    pattern: str, source: PermissionRuleSource, context: ToolPermissionContext, cwd: str
) -> tuple[str, str | None]:
    """`patternWithRoot`: `(relative_pattern, root)`; a `None` root floats under the working dir."""
    if pattern.startswith("//"):
        return pattern[1:], "/"
    if pattern.startswith("~/"):
        return pattern[1:], os.path.expanduser("~")
    if pattern.startswith("/"):
        return pattern, root_path_for_source(source, context, cwd)
    if pattern.startswith("./"):
        return pattern[2:], None
    return pattern, None


def _rules_for(context: ToolPermissionContext, behavior: RuleBehavior) -> tuple[PermissionRule, ...]:
    if behavior == "allow":
        return context.always_allow_rules
    if behavior == "deny":
        return context.always_deny_rules
    return context.always_ask_rules


def get_patterns_by_root(
    context: ToolPermissionContext, tool_type: ToolType, behavior: RuleBehavior, cwd: str
) -> dict[str | None, dict[str, PermissionRule]]:
    """`getPatternsByRoot`: every content rule on the Edit (or Read) name, grouped by root."""
    tool_name = FILE_EDIT_TOOL_NAME if tool_type == "edit" else FILE_READ_TOOL_NAME
    by_root: dict[str | None, dict[str, PermissionRule]] = {}
    for rule in _rules_for(context, behavior):
        if rule.tool_name != tool_name or not rule.rule_content:
            continue
        relative_pattern, root = pattern_with_root(rule.rule_content, rule.source, context, cwd)
        by_root.setdefault(root, {})[relative_pattern] = rule
    return by_root


def _ignore_test(patterns: Sequence[str], relative: str) -> str | None:
    """`ignore().add(patterns).test(path)`: the last matching pattern decides."""
    decided: str | None = None
    for pattern in patterns:
        compiled = _GitIgnorePattern(pattern)
        if compiled.include is None or not compiled.match_file(relative):
            continue
        decided = pattern if compiled.include else None
    return decided


def matching_rule_for_input(
    path: str,
    context: ToolPermissionContext,
    tool_type: ToolType,
    behavior: RuleBehavior,
    cwd: str,
) -> PermissionRule | None:
    """`matchingRuleForInput`: the path rule of `behavior` that matches `path`, if any."""
    absolute = expand_path(path, cwd)
    for root, pattern_map in get_patterns_by_root(context, tool_type, behavior, cwd).items():
        # The `ignore` package matches a path and everything under it, so `/**` is dropped.
        adjusted = [p[:-3] if p.endswith("/**") else p for p in pattern_map]
        relative = posixpath.relpath(absolute, root if root is not None else cwd)
        if relative.startswith("../") or relative == "..":
            continue
        if relative == ".":
            continue
        matched = _ignore_test(adjusted, relative)
        if matched is not None:
            return pattern_map.get(matched + "/**") or pattern_map.get(matched)
    return None


# ── decisions ────────────────────────────────────────────────────────────────

def is_path_allowed(
    resolved_path: str,
    context: ToolPermissionContext,
    cwd: str,
    operation: FileOperationType,
    paths_to_check: Sequence[str] | None = None,
) -> PathCheckResult:
    """
    `isPathAllowed` (`pathValidation.ts`), used for the paths inside Bash commands.

    Deny rules, then (for writes) the safety check, then the working directory — reads
    anywhere inside it, writes only in `acceptEdits` — then allow rules.
    """
    permission_type: ToolType = "read" if operation == "read" else "edit"
    deny_rule = matching_rule_for_input(resolved_path, context, permission_type, "deny", cwd)
    if deny_rule is not None:
        return PathCheckResult(False, {"type": "rule", "rule": deny_rule})

    candidates = paths_to_check if paths_to_check is not None else get_paths_for_permission_check(resolved_path)
    if operation != "read":
        safe, message, approvable = check_path_safety_for_auto_edit(resolved_path, candidates)
        if not safe:
            return PathCheckResult(
                False, {"type": "safetyCheck", "reason": message, "classifierApprovable": approvable}
            )

    if path_in_allowed_working_path(resolved_path, context, cwd, candidates):
        if operation == "read" or context.mode == PermissionMode.ACCEPT_EDITS:
            return PathCheckResult(True)

    allow_rule = matching_rule_for_input(resolved_path, context, permission_type, "allow", cwd)
    if allow_rule is not None:
        return PathCheckResult(True, {"type": "rule", "rule": allow_rule})
    return PathCheckResult(False)


def check_write_permission_for_tool(
    path: str,
    input_args: dict[str, Any],
    context: ToolPermissionContext,
    cwd: str,
) -> PermissionResult:
    """
    `checkWritePermissionForTool`, the `checkPermissions` of `WriteFile` and `Edit`.

    1. A deny rule on the path (or its resolved form) denies.
    2. The safety check asks — and the gate keeps that ask even in bypass mode.
    3. An ask rule asks.
    4. `acceptEdits` allows writes inside the working directories.
    5. An allow rule allows.
    6. Otherwise ask; headless runs turn that into a deny.
    """
    absolute = expand_path(path, cwd)
    candidates = get_paths_for_permission_check(absolute)
    for candidate in candidates:
        deny_rule = matching_rule_for_input(candidate, context, "edit", "deny", cwd)
        if deny_rule is not None:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"Permission to edit {path} has been denied.",
                decision_reason={"type": "rule", "rule": deny_rule},
            )

    safe, message, approvable = check_path_safety_for_auto_edit(absolute, candidates)
    if not safe:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=message,
            decision_reason={"type": "safetyCheck", "reason": message, "classifierApprovable": approvable},
        )

    for candidate in candidates:
        ask_rule = matching_rule_for_input(candidate, context, "edit", "ask", cwd)
        if ask_rule is not None:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=f"The agent requested permissions to write to {path}, but you haven't granted it yet.",
                decision_reason={"type": "rule", "rule": ask_rule},
            )

    in_working_dir = path_in_allowed_working_path(absolute, context, cwd, candidates)
    if context.mode == PermissionMode.ACCEPT_EDITS and in_working_dir:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "mode", "mode": context.mode},
        )

    allow_rule = matching_rule_for_input(absolute, context, "edit", "allow", cwd)
    if allow_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "rule", "rule": allow_rule},
        )

    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=f"The agent requested permissions to write to {path}, but you haven't granted it yet.",
        decision_reason=(
            None
            if in_working_dir
            else {"type": "workingDir", "reason": "Path is outside allowed working directories"}
        ),
    )


def is_project_dir_path(absolute_path: str, cwd: str) -> bool:
    """
    `isProjectDirPath`: inside `<config>/projects/<sanitized cwd>/`, where this project's
    oversized tool results are filed (`app/loop/tools/result_storage.py`).
    """
    from app.loop.tools.result_storage import config_home, sanitize_path

    project_dir = posixpath.normpath(str(config_home() / "projects" / sanitize_path(cwd)))
    normalized = posixpath.normpath(absolute_path)
    return normalized.startswith(project_dir + "/")


def check_readable_internal_path(absolute_path: str, input_args: dict[str, Any], cwd: str) -> PermissionResult:
    """
    `checkReadableInternalPath`, reduced to the one internal store this loop has: the
    project directory. Session memory, plan files, scratchpad, temp, memory, tasks, teams and
    bundled-skill paths have no equivalent here.
    """
    if is_project_dir_path(absolute_path, cwd):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "other", "reason": "Project directory files are allowed for reading"},
        )
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH)


def check_read_permission_for_tool(
    path: str,
    input_args: dict[str, Any],
    context: ToolPermissionContext,
    cwd: str,
) -> PermissionResult:
    """
    `checkReadPermissionForTool`, the `check_permissions` of `ReadFile`, `Glob` and `Grep`.

    1. A UNC path asks; 2. a suspicious Windows pattern asks (both reason 'other', so
    bypass mode allows them); 3. a `Read` deny rule denies; 4. a `Read` ask rule asks;
    5. edit access implies read access (only an 'allow' is used); 6. inside the working
    directories it allows; 7. the readable internal paths allow; 8. a `Read` allow rule
    (original path only) allows; otherwise ask with a 'workingDir' reason.
    """
    absolute = expand_path(path, cwd)
    candidates = get_paths_for_permission_check(absolute)
    for candidate in candidates:
        if candidate.startswith("\\\\") or candidate.startswith("//"):
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=(
                    f"The agent requested permissions to read from {path}, which appears to be a UNC path "
                    "that could access network resources."
                ),
                decision_reason={"type": "other", "reason": "UNC path detected (defense-in-depth check)"},
            )
    for candidate in candidates:
        if has_suspicious_windows_path_pattern(candidate):
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=(
                    f"The agent requested permissions to read from {path}, which contains a suspicious "
                    "Windows path pattern that requires manual approval."
                ),
                decision_reason={
                    "type": "other",
                    "reason": (
                        "Path contains suspicious Windows-specific patterns (alternate data streams, short "
                        "names, long path prefixes, or three or more consecutive dots) that require manual "
                        "verification"
                    ),
                },
            )
    for candidate in candidates:
        deny_rule = matching_rule_for_input(candidate, context, "read", "deny", cwd)
        if deny_rule is not None:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"Permission to read {path} has been denied.",
                decision_reason={"type": "rule", "rule": deny_rule},
            )
    for candidate in candidates:
        ask_rule = matching_rule_for_input(candidate, context, "read", "ask", cwd)
        if ask_rule is not None:
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=f"The agent requested permissions to read from {path}, but you haven't granted it yet.",
                decision_reason={"type": "rule", "rule": ask_rule},
            )
    edit_result = check_write_permission_for_tool(absolute, input_args, context, cwd)
    if edit_result.behavior == PermissionBehavior.ALLOW:
        return edit_result
    if path_in_allowed_working_path(absolute, context, cwd, candidates):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "mode", "mode": PermissionMode.DEFAULT},
        )
    internal = check_readable_internal_path(absolute, input_args, cwd)
    if internal.behavior != PermissionBehavior.PASSTHROUGH:
        return internal
    allow_rule = matching_rule_for_input(absolute, context, "read", "allow", cwd)
    if allow_rule is not None:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input=input_args,
            decision_reason={"type": "rule", "rule": allow_rule},
        )
    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=f"The agent requested permissions to read from {path}, but you haven't granted it yet.",
        decision_reason={"type": "workingDir", "reason": "Path is outside allowed working directories"},
    )


def get_file_read_ignore_patterns(context: ToolPermissionContext, cwd: str) -> dict[str | None, list[str]]:
    """`getFileReadIgnorePatterns`: every `Read` deny pattern, grouped by its root."""
    return {root: list(patterns) for root, patterns in get_patterns_by_root(context, "read", "deny", cwd).items()}


def normalize_pattern_to_path(pattern_root: str, pattern: str, root_path: str) -> str | None:
    """
    `normalizePatternToPath`: a rooted pattern re-expressed against `root_path`, or `None`
    when its root lies outside. Upstream's third branch (a root "inside the reference root
    but not starting with it") cannot be reached: a root inside `root_path` always yields a
    joined pattern that starts with it, so only its `None` outcome is kept.
    """
    if pattern_root == root_path:
        return posixpath.join("/", pattern)
    full_pattern = posixpath.join(pattern_root, pattern.lstrip("/"))
    if full_pattern.startswith(root_path + "/"):
        return full_pattern[len(root_path):]
    return None


def normalize_patterns_to_path(patterns_by_root: dict[str | None, list[str]], root: str) -> list[str]:
    """`normalizePatternsToPath`: floating patterns as they are, rooted ones re-anchored at `root`."""
    result = list(dict.fromkeys(patterns_by_root.get(None, [])))
    for pattern_root, patterns in patterns_by_root.items():
        if pattern_root is None:
            continue
        for pattern in patterns:
            normalized = normalize_pattern_to_path(pattern_root, pattern, root)
            if normalized is not None and normalized not in result:
                result.append(normalized)
    return result


def read_deny_ignore_globs(context: ToolPermissionContext, cwd: str, *, anchor_floating: bool) -> list[str]:
    """
    The `--glob !pattern` arguments that hide `Read`-denied files from a search, as `glob.ts`
    (bare `!pattern`) and `GrepTool` (`!**/pattern` for a floating pattern) build them. Both
    are anchored at the workspace root, which is where ripgrep runs.
    """
    globs: list[str] = []
    for pattern in normalize_patterns_to_path(get_file_read_ignore_patterns(context, cwd), cwd):
        if anchor_floating and not pattern.startswith("/"):
            globs += ["--glob", f"!**/{pattern}"]
        else:
            globs += ["--glob", f"!{pattern}"]
    return globs


def permission_cwd(context: ToolContext) -> str:
    """The directory path checks resolve against: the workspace root, else `os.getcwd()`."""
    root = getattr(getattr(context, "workspace", None), "root", None)
    return str(root) if root is not None else os.getcwd()


def workspace_absolute_path(raw_path: str, cwd: str) -> str:
    """
    A file tool's path as the permission check sees it. File tools read a leading `/` as
    the workspace root (`normalize_path`), so it is re-rooted there rather than read as a
    host path; `..` is kept, so an escape stays visible as outside the working directory.
    The one exception is a path into this loop's config directory (`<config>/projects/...`,
    where oversized results are filed): that is a host path, read straight off the host.
    """
    from app.loop.tools.result_storage import config_home

    expanded = os.path.expanduser(raw_path)
    config_dir = posixpath.normpath(str(config_home()))
    if posixpath.isabs(expanded) and posixpath.normpath(expanded).startswith(config_dir + "/"):
        return posixpath.normpath(expanded)
    return posixpath.normpath(posixpath.join(cwd, raw_path.replace("\\", "/").lstrip("/")))


def path_from_tool_input(input_args: dict[str, Any]) -> str:
    """A file tool's target: `file_path`, or the legacy `path`."""
    return str(input_args.get("file_path") or input_args.get("path") or "")


def check_file_write_permissions() -> Any:
    """
    Build the `check_permissions` callable of a file-writing tool. Upstream passes the tool
    for its `getPath`; here every file tool names its target `file_path`.
    """
    from app.loop.permissions.rules import extract_permission_context

    def check(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
        cwd = permission_cwd(context)
        raw = path_from_tool_input(input_args)
        return check_write_permission_for_tool(
            workspace_absolute_path(raw, cwd),
            input_args,
            extract_permission_context(context),
            cwd,
        )

    return check


def check_file_read_permissions(path_key: str) -> Any:
    """
    Build the `check_permissions` callable of a file-reading tool. `getPath` defaults to the
    working directory when the tool's path argument is absent (`ReadFile`'s `file_path`,
    `Glob`'s and `Grep`'s `path`).
    """
    from app.loop.permissions.rules import extract_permission_context

    def check(input_args: dict[str, Any], context: ToolContext) -> PermissionResult:
        cwd = permission_cwd(context)
        raw = str(input_args.get(path_key) or "")
        target = workspace_absolute_path(raw, cwd) if raw else cwd
        return check_read_permission_for_tool(target, input_args, extract_permission_context(context), cwd)

    return check


__all__ = [
    "check_file_read_permissions",
    "check_read_permission_for_tool",
    "check_readable_internal_path",
    "get_file_read_ignore_patterns",
    "is_project_dir_path",
    "normalize_pattern_to_path",
    "normalize_patterns_to_path",
    "read_deny_ignore_globs",
    "DANGEROUS_DIRECTORIES",
    "DANGEROUS_FILES",
    "FILE_EDIT_TOOL_NAME",
    "FILE_READ_TOOL_NAME",
    "PathCheckResult",
    "all_working_directories",
    "check_file_write_permissions",
    "check_path_safety_for_auto_edit",
    "check_write_permission_for_tool",
    "expand_path",
    "get_paths_for_permission_check",
    "get_patterns_by_root",
    "has_suspicious_windows_path_pattern",
    "is_dangerous_file_path_to_auto_edit",
    "is_path_allowed",
    "matching_rule_for_input",
    "normalize_case_for_comparison",
    "path_from_tool_input",
    "path_in_allowed_working_path",
    "path_in_working_path",
    "pattern_with_root",
    "permission_cwd",
    "root_path_for_source",
    "workspace_absolute_path",
]
