"""
The Bash tool's permission decision: rule matching, the security battery, read-only
auto-allow, and the redirection checks, composed as upstream composes them.

Ported from (v2.1.88), the legacy (no tree-sitter) path throughout:

- `src/tools/BashTool/bashPermissions.ts` -> `bashToolHasPermission`,
  `bashToolCheckPermission`, `bashToolCheckExactMatchPermission`,
  `checkCommandAndSuggestRules`, `matchingRulesForInput`,
  `filterRulesByContentsMatchingInput`, `stripSafeWrappers`, `stripAllLeadingEnvVars`,
  `stripCommentLines`, `filterCdCwdSubcommands`, `isNormalizedGitCommand`,
  `isNormalizedCdCommand`, `commandHasAnyCd`.
- `src/tools/BashTool/bashCommandHelpers.ts` -> `checkCommandOperatorPermissions`,
  `segmentedCommandPermissionResult`, `buildSegmentWithoutRedirections`.
- `src/tools/BashTool/modeValidation.ts` -> `checkPermissionMode`.
- `src/tools/BashTool/pathValidation.ts` -> `checkPathConstraints` (process
  substitution, dangerous redirections, `validateOutputRedirections`) and
  `src/utils/permissions/pathValidation.ts` -> `validatePath`'s pre-checks.

The per-command path validators (`validateSinglePathCommand`) live in
`bash_path_validation.py`, `checkSedConstraints` in `bash_sed_validation.py`, and the
read-only classifier (`readOnlyValidation.ts`, which `BashTool.isReadOnly` calls) in
`bash_read_only.py`; each is wired here where upstream calls it.

Not ported: sandbox auto-allow, the Haiku prompt-rule classifiers, the `BASH_CLASSIFIER`
pending checks, permission-update suggestions (they feed the approval UI), and the
`MAX_SUBCOMMANDS_FOR_SECURITY_CHECK` fan-out cap — every subcommand is checked, which is
the safe direction.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal, Mapping

from app.loop.permissions.bash_commands import (
    OutputRedirection,
    extract_output_redirections,
    get_pipe_segments,
    is_unsafe_compound_command,
    split_command,
    without_output_redirections,
)
from app.loop.permissions.bash_path_validation import (
    MAX_DIRS_TO_LIST,
    format_directory_list,
    validate_path,
    validate_single_path_command,
)
from app.loop.permissions.bash_read_only import is_bash_read_only
from app.loop.permissions.bash_sed_validation import check_sed_constraints
from app.loop.permissions.bash_security import (
    bash_command_is_safe,
    injection_check_disabled,
    strip_safe_heredoc_substitutions,
    try_parse_shell_command,
)
from app.loop.permissions.shell_rules import match_wildcard_pattern, parse_permission_rule
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    ToolPermissionContext,
)

BASH_TOOL_NAME = "Bash"

MatchMode = Literal["exact", "prefix"]

#: `SAFE_ENV_VARS`: stripped before allow-rule matching. Never PATH, LD_*, PYTHONPATH, ...
SAFE_ENV_VARS: frozenset[str] = frozenset({
    "GOEXPERIMENT", "GOOS", "GOARCH", "CGO_ENABLED", "GO111MODULE",
    "RUST_BACKTRACE", "RUST_LOG",
    "NODE_ENV",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_DEBUG",
    "ANTHROPIC_API_KEY",
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_TIME", "CHARSET",
    "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR", "TZ",
    "LS_COLORS", "LSCOLORS", "GREP_COLOR", "GREP_COLORS", "GCC_COLORS",
    "TIME_STYLE", "BLOCK_SIZE", "BLOCKSIZE",
})

#: `ANT_ONLY_SAFE_ENV_VARS`: only when `USER_TYPE=ant`, exactly as upstream gates them.
ANT_ONLY_SAFE_ENV_VARS: frozenset[str] = frozenset({
    "KUBECONFIG", "DOCKER_HOST", "AWS_PROFILE", "CLOUDSDK_CORE_PROJECT", "CLUSTER",
    "COO_CLUSTER", "COO_CLUSTER_NAME", "COO_NAMESPACE", "COO_LAUNCH_YAML_DRY_RUN",
    "SKIP_NODE_VERSION_CHECK", "EXPECTTEST_ACCEPT", "CI", "GIT_LFS_SKIP_SMUDGE",
    "CUDA_VISIBLE_DEVICES", "JAX_PLATFORMS", "COLUMNS", "TMUX",
    "POSTGRESQL_VERSION", "FIRESTORE_EMULATOR_HOST", "HARNESS_QUIET",
    "TEST_CROSSCHECK_LISTS_MATCH_UPDATE", "DBT_PER_DEVELOPER_ENVIRONMENTS", "STATSIG_FORD_DB_CHECKS",
    "ANT_ENVIRONMENT", "ANT_SERVICE", "MONOREPO_ROOT_DIR", "PYENV_VERSION",
    "PGPASSWORD", "GH_TOKEN", "GROWTHBOOK_API_KEY",
})

#: `ACCEPT_EDITS_ALLOWED_COMMANDS` (modeValidation.ts).
ACCEPT_EDITS_ALLOWED_COMMANDS: tuple[str, ...] = ("mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed")

_JS_TRIM_CHARS = (
    "\t\n\x0b\x0c\r              "
    "    　﻿"
)

_SAFE_WRAPPER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"timeout[ \t]+(?:(?:--(?:foreground|preserve-status|verbose)|--(?:kill-after|signal)=[A-Za-z0-9_.+-]+"
        r"|--(?:kill-after|signal)[ \t]+[A-Za-z0-9_.+-]+|-v|-[ks][ \t]+[A-Za-z0-9_.+-]+|-[ks][A-Za-z0-9_.+-]+)"
        r"[ \t]+)*(?:--[ \t]+)?[0-9]+(?:\.[0-9]+)?[smhd]?[ \t]+"
    ),
    re.compile(r"time[ \t]+(?:--[ \t]+)?"),
    re.compile(r"nice(?:[ \t]+-n[ \t]+-?[0-9]+|[ \t]+-[0-9]+)?[ \t]+(?:--[ \t]+)?"),
    re.compile(r"stdbuf(?:[ \t]+-[ioe][LN0-9]+)+[ \t]+(?:--[ \t]+)?"),
    re.compile(r"nohup[ \t]+(?:--[ \t]+)?"),
)
_SAFE_ENV_VAR_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_./:-]+)[ \t]+")
#: JS `\\.` without the `s` flag: a backslash and any char but a line terminator.
_JS_ESCAPE = "\\\\[^\n\r\u2028\u2029]"
_ANY_ENV_VAR_PATTERN = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)\+?="
    "(?:'[^'\n\r]*'|\"(?:" + _JS_ESCAPE + "|[^\"$`\\\\\n\r])*\"|" + _JS_ESCAPE + "|[^ \t\n\r$`;|&()<>\\\\'\"])*[ \t]+",
)
_WS = "[" + "\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff" + "]"
_PROCESS_SUBSTITUTION = re.compile(">>" + _WS + "*>" + _WS + r"*\(|>" + _WS + "*>" + _WS + r"*\(|<" + _WS + r"*\(")


def _js_trim(s: str) -> str:
    return s.strip(_JS_TRIM_CHARS)


def request_message(tool_name: str = BASH_TOOL_NAME, decision_reason: Mapping[str, Any] | None = None) -> str:
    """`createPermissionRequestMessage` for the reason shapes this flow produces."""
    if decision_reason is not None:
        reason_type = decision_reason.get("type")
        if reason_type in ("other", "safetyCheck"):
            return str(decision_reason.get("reason"))
        if reason_type == "subcommandResults":
            reasons = decision_reason.get("reasons")
            needs: list[str] = []
            if isinstance(reasons, dict):
                for cmd, result in reasons.items():
                    if result.behavior in (PermissionBehavior.ASK, PermissionBehavior.PASSTHROUGH):
                        if tool_name == BASH_TOOL_NAME:
                            extraction = extract_output_redirections(cmd)
                            needs.append(extraction.command_without_redirections if extraction.redirections else cmd)
                        else:
                            needs.append(cmd)
            if needs:
                part = "part" if len(needs) == 1 else "parts"
                verb = "requires" if len(needs) == 1 else "require"
                return (
                    f"This {tool_name} command contains multiple operations. "
                    f"The following {part} {verb} approval: {', '.join(needs)}"
                )
            return f"This {tool_name} command contains multiple operations that require approval"
    return f"The agent requested permissions to use {tool_name}, but you haven't granted it yet."


def _ask(reason: str, message: str | None = None) -> PermissionResult:
    decision = {"type": "other", "reason": reason}
    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=message if message is not None else request_message(BASH_TOOL_NAME, decision),
        decision_reason=decision,
    )


# ── wrapper and env-var stripping ────────────────────────────────────────────

def strip_comment_lines(command: str) -> str:
    """`stripCommentLines`: drop whole-line comments; all-comment input is returned as is."""
    lines = [line for line in command.split("\n") if _js_trim(line) != "" and not _js_trim(line).startswith("#")]
    return "\n".join(lines) if lines else command


def strip_safe_wrappers(command: str) -> str:
    """
    `stripSafeWrappers`: leading safe env assignments, then wrapper commands (timeout,
    time, nice, stdbuf, nohup), each to a fixed point. Env vars are not stripped after a
    wrapper — there `VAR=val` is the command, not an assignment.
    """
    stripped = command
    previous = ""
    while stripped != previous:
        previous = stripped
        stripped = strip_comment_lines(stripped)
        m = _SAFE_ENV_VAR_PATTERN.match(stripped)
        if m:
            name = m.group(1)
            ant_only = os.environ.get("USER_TYPE") == "ant" and name in ANT_ONLY_SAFE_ENV_VARS
            if name in SAFE_ENV_VARS or ant_only:
                stripped = stripped[m.end():]

    previous = ""
    while stripped != previous:
        previous = stripped
        stripped = strip_comment_lines(stripped)
        for pattern in _SAFE_WRAPPER_PATTERNS:
            stripped = pattern.sub("", stripped, count=1) if pattern.match(stripped) else stripped
    return _js_trim(stripped)


def strip_all_leading_env_vars(command: str, blocklist: re.Pattern[str] | None = None) -> str:
    """`stripAllLeadingEnvVars`: every leading assignment, for deny/ask rule matching."""
    stripped = command
    previous = ""
    while stripped != previous:
        previous = stripped
        stripped = strip_comment_lines(stripped)
        m = _ANY_ENV_VAR_PATTERN.match(stripped)
        if not m:
            continue
        if blocklist is not None and blocklist.search(m.group(1)):
            break
        stripped = stripped[m.end():]
    return _js_trim(stripped)


# ── rule matching ────────────────────────────────────────────────────────────

def get_rule_by_contents_for_tool(
    context: ToolPermissionContext, tool_name: str, behavior: PermissionBehavior
) -> dict[str, PermissionRule]:
    """`getRuleByContentsForToolName`: content-bearing rules for the tool, keyed by content."""
    source = {
        PermissionBehavior.ALLOW: context.always_allow_rules,
        PermissionBehavior.DENY: context.always_deny_rules,
        PermissionBehavior.ASK: context.always_ask_rules,
    }[behavior]
    by_content: dict[str, PermissionRule] = {}
    for rule in source:
        if rule.tool_name == tool_name and rule.rule_content is not None and rule.rule_behavior == behavior:
            by_content[rule.rule_content] = rule
    return by_content


def filter_rules_by_contents_matching_input(
    command: str,
    rules: dict[str, PermissionRule],
    match_mode: MatchMode,
    *,
    strip_all_env_vars: bool = False,
    skip_compound_check: bool = False,
) -> list[PermissionRule]:
    """`filterRulesByContentsMatchingInput`."""
    command = _js_trim(command)
    without_redirections = extract_output_redirections(command).command_without_redirections
    for_matching = [command, without_redirections] if match_mode == "exact" else [without_redirections]

    candidates: list[str] = []
    for cmd in for_matching:
        stripped = strip_safe_wrappers(cmd)
        candidates.extend([cmd, stripped] if stripped != cmd else [cmd])

    if strip_all_env_vars:
        seen = set(candidates)
        start = 0
        while start < len(candidates):
            end = len(candidates)
            for idx in range(start, end):
                cmd = candidates[idx]
                if not cmd:
                    continue
                for variant in (strip_all_leading_env_vars(cmd), strip_safe_wrappers(cmd)):
                    if variant not in seen:
                        candidates.append(variant)
                        seen.add(variant)
            start = end

    compound: dict[str, bool] = {}
    if match_mode == "prefix" and not skip_compound_check:
        for cmd in candidates:
            if cmd not in compound:
                compound[cmd] = len(split_command(cmd)) > 1

    def matches(content: str) -> bool:
        rule = parse_permission_rule(content)
        for cmd in candidates:
            if rule.type == "exact":
                if rule.value == cmd:
                    return True
            elif rule.type == "prefix":
                if match_mode == "exact":
                    if rule.value == cmd:
                        return True
                    continue
                if compound.get(cmd):
                    continue
                if cmd == rule.value or cmd.startswith(rule.value + " "):
                    return True
                xargs = "xargs " + rule.value
                if cmd == xargs or cmd.startswith(xargs + " "):
                    return True
            else:
                if match_mode == "exact" or compound.get(cmd):
                    continue
                if match_wildcard_pattern(rule.value, cmd):
                    return True
        return False

    return [rule for content, rule in rules.items() if matches(content)]


def matching_rules_for_input(
    command: str,
    context: ToolPermissionContext,
    match_mode: MatchMode,
    *,
    skip_compound_check: bool = False,
) -> tuple[list[PermissionRule], list[PermissionRule], list[PermissionRule]]:
    """`matchingRulesForInput` -> (deny, ask, allow). Deny/ask strip every env var."""
    deny = filter_rules_by_contents_matching_input(
        command,
        get_rule_by_contents_for_tool(context, BASH_TOOL_NAME, PermissionBehavior.DENY),
        match_mode,
        strip_all_env_vars=True,
        skip_compound_check=True,
    )
    ask = filter_rules_by_contents_matching_input(
        command,
        get_rule_by_contents_for_tool(context, BASH_TOOL_NAME, PermissionBehavior.ASK),
        match_mode,
        strip_all_env_vars=True,
        skip_compound_check=True,
    )
    allow = filter_rules_by_contents_matching_input(
        command,
        get_rule_by_contents_for_tool(context, BASH_TOOL_NAME, PermissionBehavior.ALLOW),
        match_mode,
        skip_compound_check=skip_compound_check,
    )
    return deny, ask, allow


def _deny_for_rule(command: str, rule: PermissionRule) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.DENY,
        message=f"Permission to use {BASH_TOOL_NAME} with command {command} has been denied.",
        decision_reason={"type": "rule", "rule": rule},
    )


def _ask_for_rule(rule: PermissionRule) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=request_message(),
        decision_reason={"type": "rule", "rule": rule},
    )


def _allow_for_rule(command: str, rule: PermissionRule) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.ALLOW,
        updated_input={"command": command},
        decision_reason={"type": "rule", "rule": rule},
    )


def _requires_approval() -> PermissionResult:
    decision = {"type": "other", "reason": "This command requires approval"}
    return PermissionResult(
        behavior=PermissionBehavior.PASSTHROUGH,
        message=request_message(BASH_TOOL_NAME, decision),
        decision_reason=decision,
    )


def bash_tool_check_exact_match_permission(command: str, context: ToolPermissionContext) -> PermissionResult:
    """`bashToolCheckExactMatchPermission`: deny, then ask, then allow, else passthrough."""
    trimmed = _js_trim(command)
    deny, ask, allow = matching_rules_for_input(command, context, "exact")
    if deny:
        return _deny_for_rule(trimmed, deny[0])
    if ask:
        return _ask_for_rule(ask[0])
    if allow:
        return _allow_for_rule(command, allow[0])
    return _requires_approval()


# ── mode and path checks ─────────────────────────────────────────────────────

def check_permission_mode(command: str, context: ToolPermissionContext) -> PermissionResult:
    """`checkPermissionMode`: acceptEdits auto-allows the filesystem commands."""
    if context.mode == PermissionMode.BYPASS_PERMISSIONS:
        return PermissionResult(
            behavior=PermissionBehavior.PASSTHROUGH, message="Bypass mode is handled in main permission flow"
        )
    if context.mode == PermissionMode.DONT_ASK:
        return PermissionResult(
            behavior=PermissionBehavior.PASSTHROUGH, message="DontAsk mode is handled in main permission flow"
        )
    for cmd in split_command(command):
        parts = re.split("[" + _JS_TRIM_CHARS + "]+", _js_trim(cmd))
        base = parts[0] if parts else ""
        if not base:
            continue
        if context.mode == PermissionMode.ACCEPT_EDITS and base in ACCEPT_EDITS_ALLOWED_COMMANDS:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                updated_input={"command": cmd},
                decision_reason={"type": "mode", "mode": PermissionMode.ACCEPT_EDITS},
            )
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="No mode-specific validation required")


def validate_output_redirections(
    redirections: tuple[OutputRedirection, ...],
    cwd: str,
    context: ToolPermissionContext,
    compound_command_has_cd: bool = False,
) -> PermissionResult:
    """`validateOutputRedirections`."""
    if compound_command_has_cd and redirections:
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=(
                "Commands that change directories and write via output redirection require explicit approval "
                "to ensure paths are evaluated correctly. For security, TDDAgents cannot automatically "
                "determine the final working directory when 'cd' is used in compound commands."
            ),
            decision_reason={
                "type": "other",
                "reason": "Compound command contains cd with output redirection - manual approval required "
                "to prevent path resolution bypass",
            },
        )
    for redirection in redirections:
        if redirection.target == "/dev/null":
            continue
        allowed, resolved, reason = validate_path(redirection.target, cwd, context, "create")
        if allowed:
            continue
        reason_type = reason.get("type") if reason else None
        if reason_type == "rule":
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=f"Output redirection to '{resolved}' was blocked by a deny rule.",
                decision_reason=reason,
            )
        if reason is not None and reason_type in ("other", "safetyCheck"):
            message = str(reason.get("reason"))
        else:
            dirs = format_directory_list([cwd, *context.additional_working_directories])
            message = (
                f"Output redirection to '{resolved}' was blocked. For security, TDDAgents may only write to "
                f"files in the allowed working directories for this session: {dirs}."
            )
        return PermissionResult(behavior=PermissionBehavior.ASK, message=message, decision_reason=reason)
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="No unsafe redirections found")


def check_path_constraints(
    command: str,
    cwd: str,
    context: ToolPermissionContext,
    compound_command_has_cd: bool = False,
) -> PermissionResult:
    """`checkPathConstraints`: process substitution, redirections, then every subcommand's paths."""
    if _PROCESS_SUBSTITUTION.search(command):
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            message=(
                "Process substitution (>(...) or <(...)) can execute arbitrary commands "
                "and requires manual approval"
            ),
            decision_reason={"type": "other", "reason": "Process substitution requires manual approval"},
        )
    extraction = extract_output_redirections(command)
    if extraction.has_dangerous_redirection:
        reason = "Shell expansion syntax in paths requires manual approval"
        return PermissionResult(
            behavior=PermissionBehavior.ASK, message=reason, decision_reason={"type": "other", "reason": reason}
        )
    result = validate_output_redirections(extraction.redirections, cwd, context, compound_command_has_cd)
    if result.behavior != PermissionBehavior.PASSTHROUGH:
        return result
    for cmd in split_command(command):
        result = validate_single_path_command(cmd, cwd, context, compound_command_has_cd)
        if result.behavior in (PermissionBehavior.ASK, PermissionBehavior.DENY):
            return result
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="All path commands validated successfully")


# ── per-subcommand checks ────────────────────────────────────────────────────

def bash_tool_check_permission(
    command: str,
    context: ToolPermissionContext,
    cwd: str,
    compound_command_has_cd: bool = False,
) -> PermissionResult:
    """`bashToolCheckPermission`: rules, path constraints, mode, then read-only."""
    trimmed = _js_trim(command)
    exact = bash_tool_check_exact_match_permission(command, context)
    if exact.behavior in (PermissionBehavior.DENY, PermissionBehavior.ASK):
        return exact

    deny, ask, allow = matching_rules_for_input(command, context, "prefix")
    if deny:
        return _deny_for_rule(trimmed, deny[0])
    if ask:
        return _ask_for_rule(ask[0])

    path_result = check_path_constraints(command, cwd, context, compound_command_has_cd)
    if path_result.behavior != PermissionBehavior.PASSTHROUGH:
        return path_result

    if exact.behavior == PermissionBehavior.ALLOW:
        return exact
    if allow:
        return _allow_for_rule(command, allow[0])

    sed_result = check_sed_constraints(command, context)
    if sed_result.behavior != PermissionBehavior.PASSTHROUGH:
        return sed_result

    mode_result = check_permission_mode(command, context)
    if mode_result.behavior != PermissionBehavior.PASSTHROUGH:
        return mode_result

    if is_bash_read_only({"command": command}, cwd):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input={"command": command},
            decision_reason={"type": "other", "reason": "Read-only command is allowed"},
        )
    return _requires_approval()


def check_command_and_suggest_rules(
    command: str,
    context: ToolPermissionContext,
    cwd: str,
    compound_command_has_cd: bool = False,
) -> PermissionResult:
    """`checkCommandAndSuggestRules` (no prefix suggestions: there is no approval UI)."""
    exact = bash_tool_check_exact_match_permission(command, context)
    if exact.behavior != PermissionBehavior.PASSTHROUGH:
        return exact

    result = bash_tool_check_permission(command, context, cwd, compound_command_has_cd)
    if result.behavior in (PermissionBehavior.DENY, PermissionBehavior.ASK):
        return result

    if not injection_check_disabled():
        safety = bash_command_is_safe(command)
        if safety.behavior != PermissionBehavior.PASSTHROUGH:
            reason = (
                safety.message
                if safety.behavior == PermissionBehavior.ASK and safety.message
                else "This command contains patterns that could pose security risks and requires approval"
            )
            return _ask(reason)

    return result


# ── command identity ─────────────────────────────────────────────────────────

def is_normalized_git_command(command: str) -> bool:
    """`isNormalizedGitCommand`: git after wrappers, env vars and quotes; also `xargs git`."""
    if command.startswith("git ") or command == "git":
        return True
    stripped = strip_safe_wrappers(command)
    parsed = try_parse_shell_command(stripped)
    if parsed.success and parsed.tokens:
        first = parsed.tokens[0]
        if first == "git":
            return True
        return first == "xargs" and "git" in parsed.tokens
    return re.match(r"git(?:[" + _JS_TRIM_CHARS + r"]|\Z)", stripped) is not None


def is_normalized_cd_command(command: str) -> bool:
    """`isNormalizedCdCommand`: cd, pushd or popd after wrappers, env vars and quotes."""
    stripped = strip_safe_wrappers(command)
    parsed = try_parse_shell_command(stripped)
    if parsed.success and parsed.tokens:
        return parsed.tokens[0] in ("cd", "pushd", "popd")
    return re.match(r"(?:cd|pushd|popd)(?:[" + _JS_TRIM_CHARS + r"]|\Z)", stripped) is not None


def command_has_any_cd(command: str) -> bool:
    """`commandHasAnyCd`."""
    return any(is_normalized_cd_command(_js_trim(sub)) for sub in split_command(command))


_MULTIPLE_CD_REASON = "Multiple directory changes in one command require approval for clarity"
_CD_GIT_REASON = "Compound commands with cd and git require approval to prevent bare repository attacks"


# ── pipes (bashCommandHelpers.ts) ────────────────────────────────────────────

def _segmented_command_permission_result(
    command: str, segments: list[str], context: ToolPermissionContext, cwd: str
) -> PermissionResult:
    """`segmentedCommandPermissionResult`: each pipe segment through the full check."""
    cd_segments = [s for s in segments if is_normalized_cd_command(_js_trim(s))]
    if len(cd_segments) > 1:
        return _ask(_MULTIPLE_CD_REASON)

    has_cd = False
    has_git = False
    for segment in segments:
        for sub in split_command(segment):
            if is_normalized_cd_command(_js_trim(sub)):
                has_cd = True
            if is_normalized_git_command(_js_trim(sub)):
                has_git = True
    if has_cd and has_git:
        return _ask(_CD_GIT_REASON)

    results: dict[str, PermissionResult] = {}
    for segment in segments:
        trimmed = _js_trim(segment)
        if not trimmed:
            continue
        results[trimmed] = bash_tool_has_permission(trimmed, context, cwd)

    for seg_command, seg_result in results.items():
        if seg_result.behavior == PermissionBehavior.DENY:
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                message=seg_result.message or f"Permission denied for: {seg_command}",
                decision_reason={"type": "subcommandResults", "reasons": results},
            )
    if all(r.behavior == PermissionBehavior.ALLOW for r in results.values()):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input={"command": command},
            decision_reason={"type": "subcommandResults", "reasons": results},
        )
    decision = {"type": "subcommandResults", "reasons": results}
    return PermissionResult(
        behavior=PermissionBehavior.ASK,
        message=request_message(BASH_TOOL_NAME, decision),
        decision_reason=decision,
    )


def check_command_operator_permissions(command: str, context: ToolPermissionContext, cwd: str) -> PermissionResult:
    """`checkCommandOperatorPermissions` on the regex `ParsedCommand`."""
    if is_unsafe_compound_command(command):
        safety = bash_command_is_safe(command)
        reason = (
            safety.message
            if safety.behavior == PermissionBehavior.ASK and safety.message
            else "This command uses shell operators that require approval for safety"
        )
        return _ask(reason)
    segments = get_pipe_segments(command)
    if len(segments) <= 1:
        return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="No pipes found in command")
    stripped_segments = [without_output_redirections(s) for s in segments]
    return _segmented_command_permission_result(command, stripped_segments, context, cwd)


# ── bashToolHasPermission ────────────────────────────────────────────────────

def bash_tool_has_permission(command: str, context: ToolPermissionContext, cwd: str) -> PermissionResult:
    """
    `bashToolHasPermission` on the legacy path. Returns 'allow', 'deny', 'ask', or
    'passthrough' (no rule resolved it and it is not read-only); the runtime gate turns
    passthrough into ask, and a headless context turns ask into deny.
    """
    parsed = try_parse_shell_command(command)
    if not parsed.success:
        return _ask(f"Command contains malformed syntax that cannot be parsed: {parsed.error}")

    exact = bash_tool_check_exact_match_permission(command, context)
    if exact.behavior == PermissionBehavior.DENY:
        return exact

    operator_result = check_command_operator_permissions(command, context, cwd)
    if operator_result.behavior != PermissionBehavior.PASSTHROUGH:
        if operator_result.behavior == PermissionBehavior.ALLOW:
            if not injection_check_disabled():
                safety = bash_command_is_safe(command)
                if safety.behavior not in (PermissionBehavior.PASSTHROUGH, PermissionBehavior.ALLOW):
                    return _ask(safety.message or "Command contains patterns that require approval")
            path_result = check_path_constraints(command, cwd, context, command_has_any_cd(command))
            if path_result.behavior != PermissionBehavior.PASSTHROUGH:
                return path_result
        return operator_result

    if not injection_check_disabled():
        original = bash_command_is_safe(command)
        if original.behavior == PermissionBehavior.ASK and original.is_bash_security_check_for_misparsing:
            remainder = strip_safe_heredoc_substitutions(command)
            remainder_result = bash_command_is_safe(remainder) if remainder is not None else None
            if remainder_result is None or (
                remainder_result.behavior == PermissionBehavior.ASK
                and remainder_result.is_bash_security_check_for_misparsing
            ):
                if exact.behavior == PermissionBehavior.ALLOW:
                    return exact
                return _ask(original.message)

    subcommands = [s for s in split_command(command) if s != f"cd {cwd}"]

    cd_commands = [s for s in subcommands if is_normalized_cd_command(s)]
    if len(cd_commands) > 1:
        return _ask(_MULTIPLE_CD_REASON)
    compound_has_cd = bool(cd_commands)
    if compound_has_cd and any(is_normalized_git_command(_js_trim(s)) for s in subcommands):
        return _ask(_CD_GIT_REASON)

    decisions = [bash_tool_check_permission(s, context, cwd, compound_has_cd) for s in subcommands]
    if any(d.behavior == PermissionBehavior.DENY for d in decisions):
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message=f"Permission to use {BASH_TOOL_NAME} with command {command} has been denied.",
            decision_reason={"type": "subcommandResults", "reasons": dict(zip(subcommands, decisions))},
        )

    path_result = check_path_constraints(command, cwd, context, compound_has_cd)
    if path_result.behavior == PermissionBehavior.DENY:
        return path_result

    ask_result = next((d for d in decisions if d.behavior == PermissionBehavior.ASK), None)
    non_allow = sum(1 for d in decisions if d.behavior != PermissionBehavior.ALLOW)
    if path_result.behavior == PermissionBehavior.ASK and ask_result is None:
        return path_result
    if ask_result is not None and non_allow == 1:
        return ask_result

    if exact.behavior == PermissionBehavior.ALLOW:
        return exact

    possible_injection = False
    if not injection_check_disabled():
        possible_injection = any(
            bash_command_is_safe(s).behavior != PermissionBehavior.PASSTHROUGH for s in subcommands
        )
    if all(d.behavior == PermissionBehavior.ALLOW for d in decisions) and not possible_injection:
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input={"command": command},
            decision_reason={"type": "subcommandResults", "reasons": dict(zip(subcommands, decisions))},
        )

    if len(subcommands) == 1:
        return check_command_and_suggest_rules(subcommands[0], context, cwd, compound_has_cd)

    results = {s: check_command_and_suggest_rules(s, context, cwd, compound_has_cd) for s in subcommands}
    if all(r.behavior == PermissionBehavior.ALLOW for r in results.values()):
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            updated_input={"command": command},
            decision_reason={"type": "subcommandResults", "reasons": results},
        )
    decision = {"type": "subcommandResults", "reasons": results}
    return PermissionResult(
        behavior=PermissionBehavior.ASK if ask_result is not None else PermissionBehavior.PASSTHROUGH,
        message=request_message(BASH_TOOL_NAME, decision),
        decision_reason=decision,
    )


__all__ = [
    "ACCEPT_EDITS_ALLOWED_COMMANDS",
    "MAX_DIRS_TO_LIST",
    "ANT_ONLY_SAFE_ENV_VARS",
    "BASH_TOOL_NAME",
    "SAFE_ENV_VARS",
    "bash_tool_check_exact_match_permission",
    "bash_tool_check_permission",
    "bash_tool_has_permission",
    "check_command_and_suggest_rules",
    "check_command_operator_permissions",
    "check_path_constraints",
    "check_permission_mode",
    "command_has_any_cd",
    "filter_rules_by_contents_matching_input",
    "format_directory_list",
    "get_rule_by_contents_for_tool",
    "is_normalized_cd_command",
    "is_normalized_git_command",
    "matching_rules_for_input",
    "request_message",
    "strip_all_leading_env_vars",
    "strip_comment_lines",
    "strip_safe_wrappers",
    "validate_output_redirections",
]
