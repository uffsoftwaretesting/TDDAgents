"""
Whether one Bash command runs in the sandbox.

Ported from `reference/claude-code/src/tools/BashTool/shouldUseSandbox.ts` -> `shouldUseSandbox`,
`containsExcludedCommand`: no when sandboxing is off; no when the call sets `dangerouslyDisableSandbox`
and the settings allow unsandboxed commands; no for an empty command; no when a subcommand matches one of
`sandbox.excludedCommands` (a convenience, not a security boundary: each `&&`/`;`/`|` part is checked
with its leading environment assignments and safe wrappers stripped, to a fixed point, against each
pattern as a prefix, exact or wildcard rule); yes otherwise.

Not ported: the ant-only dynamic disabled-commands config (`tengu_sandbox_disabled_commands`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.tools.bash_tool.bash_permissions import (
    BINARY_HIJACK_VARS,
    strip_all_leading_env_vars,
    strip_safe_wrappers,
)
from app.utils.bash.commands import split_command
from app.utils.permissions.shell_rule_matching import match_wildcard_pattern, parse_permission_rule


def _candidates(subcommand: str) -> list[str]:
    """The subcommand, then every form the two strippings reach from it, until nothing new appears."""
    candidates = [subcommand]
    seen = {subcommand}
    start = 0
    while start < len(candidates):
        end = len(candidates)
        for command in candidates[start:end]:
            for stripped in (strip_all_leading_env_vars(command, BINARY_HIJACK_VARS), strip_safe_wrappers(command)):
                if stripped not in seen:
                    candidates.append(stripped)
                    seen.add(stripped)
        start = end
    return candidates


def contains_excluded_command(command: str, excluded_commands: list[str]) -> bool:
    """`containsExcludedCommand`: whether any subcommand matches a `sandbox.excludedCommands` pattern."""
    if not excluded_commands:
        return False
    try:
        subcommands = split_command(command)
    except Exception:  # noqa: BLE001 - upstream falls back to the whole command when it cannot split it
        subcommands = [command]
    for subcommand in subcommands:
        candidates = _candidates(subcommand.strip())
        for pattern in excluded_commands:
            rule = parse_permission_rule(pattern)
            for candidate in candidates:
                if rule.type == "prefix" and (candidate == rule.value or candidate.startswith(rule.value + " ")):
                    return True
                if rule.type == "exact" and candidate == rule.value:
                    return True
                if rule.type == "wildcard" and match_wildcard_pattern(rule.value, candidate):
                    return True
    return False


def should_use_sandbox(input_args: Mapping[str, Any], sandbox: Any) -> bool:
    """`shouldUseSandbox` against the run's sandbox adapter (`None` is no sandbox)."""
    if sandbox is None or not sandbox.is_sandboxing_enabled():
        return False
    if input_args.get("dangerouslyDisableSandbox") and sandbox.are_unsandboxed_commands_allowed():
        return False
    command = input_args.get("command")
    if not command:
        return False
    return not contains_excluded_command(str(command), sandbox.get_excluded_commands())


__all__ = ["contains_excluded_command", "should_use_sandbox"]
