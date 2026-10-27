"""
The choices a permission prompt offers, per tool, as upstream's permission dialogs build them
(Phase 11). UI-neutral: each option is a value, a label, and the permission updates choosing
it applies; the REPL renders and reads them.

Ported from `reference/claude-code/src/components/permissions/`:

* `FallbackPermissionRequest.tsx` — Yes / Yes, and don't ask again for <tool> commands in
  <cwd> (an allow rule for the whole tool, `localSettings`) / No.
* `BashPermissionRequest/bashToolUseOptions.tsx` — Yes / the result's suggestions, labelled
  by `shellPermissionHelpers.tsx` -> `generateShellSuggestionsLabel` / No.
* `FilePermissionDialog/permissionOptions.tsx` -> `getFilePermissionOptions` — Yes / Yes,
  during this session (`filesystem.ts` -> `generateSuggestions`) / No.
* `ExitPlanModePermissionRequest/` for `UpdatePlan` — the plan change is approved or
  rejected, never remembered.

The "always" options are hidden under `allowManagedPermissionRulesOnly`
(`shouldShowAlwaysAllowOptions`). Every "No" takes feedback. Not ported, all UI: the editable
Bash prefix, the classifier-description option (ant-only), the `.claude/` folder option, the
input-mode "Yes, and tell Claude what to do next", keyboard shortcuts in labels.
"""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.tdd.tdd_plan_tool.tdd_plan_tool import UPDATE_PLAN_TOOL_NAME
from app.tools.bash_tool.bash_permissions import BASH_TOOL_NAME
from app.types.permissions import (
    PermissionBehavior,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.bash.commands import extract_output_redirections
from app.utils.permissions.filesystem import (
    generate_suggestions,
    get_directory_for_path,
    path_in_allowed_working_path,
    workspace_absolute_path,
)
from app.utils.permissions.permission_update import PermissionUpdate
from app.utils.permissions.shell_rule_matching import permission_rule_extract_prefix
from app.utils.settings.settings import should_allow_managed_permission_rules_only

#: `FileOperationType`.
FileOperation = Literal["read", "write", "create"]

#: The tools whose prompt is the file dialog, and the operation each one asks for.
FILE_TOOL_OPERATIONS: dict[str, FileOperation] = {
    "WriteFile": "write", "Edit": "write", "ReadFile": "read", "Glob": "read", "Grep": "read",
}


@dataclass(frozen=True, slots=True)
class PermissionOption:
    """One choice: what the user sees, and what choosing it does."""

    value: str
    label: str
    #: The permission updates "allow" applies (`onAllow(input, updates)`); empty for a plain yes.
    updates: tuple[Any, ...] = ()
    #: A "No": the call is rejected, with the user's feedback when they give some.
    reject: bool = False


YES = PermissionOption("yes", "Yes")
NO = PermissionOption("no", "No", reject=True)


def should_show_always_allow_options(context: ToolPermissionContext) -> bool:
    """`shouldShowAlwaysAllowOptions`: not under `allowManagedPermissionRulesOnly`."""
    locations = context.settings_locations
    return locations is None or not should_allow_managed_permission_rules_only(locations)


def fallback_permission_options(tool_name: str, cwd: str, show_always: bool) -> list[PermissionOption]:
    """`FallbackPermissionRequest`'s options."""
    options = [YES]
    if show_always:
        update = PermissionUpdate("addRules", PermissionRuleSource.LOCAL_SETTINGS, rules=((tool_name, None),),
                                  behavior=PermissionBehavior.ALLOW)
        options.append(PermissionOption(
            "yes-dont-ask-again", f"Yes, and don't ask again for {tool_name} commands in {cwd}", (update,)))
    return [*options, NO]


def _strip_bash_redirections(command: str) -> str:
    """`stripBashRedirections`: drop output redirections so a filename never reads as a command."""
    extraction = extract_output_redirections(command)
    return extraction.command_without_redirections if extraction.redirections else command


def _command_list_display(commands: Sequence[str]) -> str:
    """`commandListDisplay`."""
    if len(commands) <= 2:
        return " and ".join(commands)
    return f"{', '.join(commands[:-1])}, and {commands[-1]}"


def _command_list_display_truncated(commands: Sequence[str]) -> str:
    """`commandListDisplayTruncated`: "similar" once the list is longer than 50 characters."""
    return "similar" if len(", ".join(commands)) > 50 else _command_list_display(commands)


def _format_path_list(paths: Sequence[str]) -> str:
    """`formatPathList`: directory names with a trailing `/`; three or more as "and N more"."""
    names = [f"{posixpath.basename(p) or p}/" for p in paths]
    if len(names) <= 2:
        return " and ".join(names)
    return f"{names[0]}, {names[1]} and {len(paths) - 2} more"


def generate_shell_suggestions_label(suggestions: Sequence[Any], shell_tool_name: str, cwd: str) -> str | None:
    """`generateShellSuggestionsLabel`: what "don't ask again" covers, in words."""
    rules = [rule for s in suggestions if s.type == "addRules" for rule in s.rules]
    read_paths = [content.replace("/**", "") for name, content in rules if name == "Read" and content]
    read_paths = [p for p in read_paths if p]
    directories = [d for s in suggestions if s.type == "addDirectories" for d in s.directories]
    commands = list(dict.fromkeys(
        _strip_bash_redirections(permission_rule_extract_prefix(content) or content)
        for name, content in rules if name == shell_tool_name and content
    ))
    if read_paths and not directories and not commands:
        return f"Yes, allow reading from {_format_path_list(read_paths)} from this project"
    if directories and not read_paths and not commands:
        return f"Yes, and always allow access to {_format_path_list(directories)} from this project"
    if commands and not directories and not read_paths:
        return f"Yes, and don't ask again for {_command_list_display_truncated(commands)} commands in {cwd}"
    paths = [*directories, *read_paths]
    if paths and not commands and directories and read_paths:
        return f"Yes, and always allow access to {_format_path_list(paths)} from this project"
    if paths and commands:
        if len(paths) == 1 and len(commands) == 1:
            return (f"Yes, and allow access to {_format_path_list(paths)} and "
                    f"{_command_list_display_truncated(commands)} commands")
        return (f"Yes, and allow {_format_path_list(paths)} access and "
                f"{_command_list_display_truncated(commands)} commands")
    return None


def bash_tool_use_options(suggestions: Sequence[Any] | None, show_always: bool, cwd: str) -> list[PermissionOption]:
    """`bashToolUseOptions`: Yes, the suggestions when there are any, No."""
    options = [YES]
    if show_always and suggestions:
        label = generate_shell_suggestions_label(suggestions, BASH_TOOL_NAME, cwd)
        if label:
            options.append(PermissionOption("yes-apply-suggestions", label, tuple(suggestions)))
    return [*options, NO]


def file_permission_options(
    path: str, operation: FileOperation, context: ToolPermissionContext, cwd: str
) -> list[PermissionOption]:
    """`getFilePermissionOptions`: Yes, Yes during this session (`generateSuggestions`), No."""
    absolute = workspace_absolute_path(path, cwd) if path else cwd
    if path_in_allowed_working_path(absolute, context, cwd):
        label = "Yes, during this session" if operation == "read" else "Yes, allow all edits during this session"
    else:
        name = posixpath.basename(get_directory_for_path(absolute)) or "this directory"
        label = (f"Yes, allow reading from {name}/ during this session" if operation == "read"
                 else f"Yes, allow all edits in {name}/ during this session")
    session = PermissionOption("yes-session", label, generate_suggestions(absolute, operation, context, cwd))
    return [YES, session, NO]


def permission_options(
    tool_name: str, input_args: dict[str, Any], suggestions: Sequence[Any] | None,
    context: ToolPermissionContext, cwd: str,
) -> list[PermissionOption]:
    """`PermissionRequest`'s per-tool dialog, reduced to its options."""
    show_always = should_show_always_allow_options(context)
    if tool_name == UPDATE_PLAN_TOOL_NAME:
        return [YES, NO]
    if tool_name == BASH_TOOL_NAME:
        return bash_tool_use_options(suggestions, show_always, cwd)
    operation = FILE_TOOL_OPERATIONS.get(tool_name)
    if operation is not None:
        path = str(input_args.get("file_path") or input_args.get("path") or "")
        return file_permission_options(path, operation, context, cwd)
    return fallback_permission_options(tool_name, cwd, show_always)
