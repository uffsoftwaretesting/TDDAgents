"""
Allow rules that would let a call skip the automated judge — and their removal.

Ported from claude-code v2.1.88:

* `src/utils/permissions/dangerousPatterns.ts` -> `CROSS_PLATFORM_CODE_EXEC`,
  `DANGEROUS_BASH_PATTERNS` (the external build: no ant-only entries)
* `src/utils/permissions/permissionSetup.ts` -> `isDangerousBashPermission`,
  `isDangerousTaskPermission`, `findDangerousClassifierPermissions`,
  `isPermissionUpdateDestination`, `removeDangerousPermissions`,
  `stripDangerousPermissionsForAutoMode`

**Why upstream has this.** An allow rule like `Bash(python:*)` lets the model run arbitrary
code through that interpreter, and an allow rule is consulted *before* the auto-mode
classifier, so the classifier never sees the call. Upstream therefore strips such rules on
entry to auto mode.

**Why TDDAgents applies it to every run (the TDD adaptation).** The judge here is not a
classifier but the phase gate (barrier 2), and it has the same blind spot: it classifies a
write by its target path — a `WriteFile` path, a Bash redirection target — and
`python -c "open('calc.py','w').write(...)"` names no target it can see. A settings allow
for an interpreter would put production code on disk during RED without any check firing.
So the TDD managed tier is upstream's own stripping, run unconditionally: same predicate,
same pattern list, same source filter.

The source filter is ported as is, and it is a real limit worth stating: upstream removes
only rules from destinations it can edit (`isPermissionUpdateDestination`), so a dangerous
allow in **policy** or **flag** settings survives. That is an operator's explicit decision,
in a file the agent cannot write, and upstream honours it too.

PowerShell has no tool in this loop, so `isDangerousPowerShellPermission` is not ported.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.loop.permissions.messages import permission_rule_value_to_string
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

logger = logging.getLogger("TDDOrchestrator.Permissions")

BASH_TOOL_NAME = "Bash"
AGENT_TOOL_NAME = "Agent"

#: `CROSS_PLATFORM_CODE_EXEC`: interpreters, package runners, shells, ssh.
CROSS_PLATFORM_CODE_EXEC: tuple[str, ...] = (
    "python",
    "python3",
    "python2",
    "node",
    "deno",
    "tsx",
    "ruby",
    "perl",
    "php",
    "lua",
    "npx",
    "bunx",
    "npm run",
    "yarn run",
    "pnpm run",
    "bun run",
    "bash",
    "sh",
    "ssh",
)

#: `DANGEROUS_BASH_PATTERNS` for the external build.
DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    *CROSS_PLATFORM_CODE_EXEC,
    "zsh",
    "fish",
    "eval",
    "exec",
    "env",
    "xargs",
    "sudo",
)

#: `isPermissionUpdateDestination`: the sources a rule can be removed from.
PERMISSION_UPDATE_DESTINATIONS: frozenset[PermissionRuleSource] = frozenset({
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.SESSION,
    PermissionRuleSource.CLI_ARG,
})


def is_dangerous_bash_permission(tool_name: str, rule_content: str | None) -> bool:
    """`isDangerousBashPermission`: a Bash allow rule that admits arbitrary code."""
    if tool_name != BASH_TOOL_NAME:
        return False
    if rule_content is None or rule_content == "":
        # Tool-level allow (Bash, Bash(*)): every command.
        return True
    content = rule_content.strip().lower()
    if content == "*":
        return True
    for pattern in DANGEROUS_BASH_PATTERNS:
        lower = pattern.lower()
        if content in (lower, f"{lower}:*", f"{lower}*", f"{lower} *"):
            return True
        # "python -*" matches "python -c 'code'".
        if content.startswith(f"{lower} -") and content.endswith("*"):
            return True
    return False


def is_dangerous_task_permission(tool_name: str, rule_content: str | None) -> bool:
    """`isDangerousTaskPermission`: any `Agent` allow rule (legacy `Task` included)."""
    from app.loop.permissions.settings import normalize_legacy_tool_name

    return normalize_legacy_tool_name(tool_name) == AGENT_TOOL_NAME


def is_dangerous_classifier_permission(tool_name: str, rule_content: str | None) -> bool:
    """`isDangerousClassifierPermission`, without the PowerShell and ant-only branches."""
    return is_dangerous_bash_permission(tool_name, rule_content) or is_dangerous_task_permission(
        tool_name, rule_content
    )


def find_dangerous_classifier_permissions(rules: tuple[PermissionRule, ...]) -> tuple[PermissionRule, ...]:
    """`findDangerousClassifierPermissions`: the dangerous allow rules, in order."""
    return tuple(
        rule
        for rule in rules
        if rule.rule_behavior == PermissionBehavior.ALLOW
        and is_dangerous_classifier_permission(rule.tool_name, rule.rule_content)
    )


def strip_dangerous_permissions(context: ToolPermissionContext) -> ToolPermissionContext:
    """
    `stripDangerousPermissionsForAutoMode`, run for every TDD context.

    Removes each dangerous allow rule whose source is editable, appends the removed rules
    to `stripped_dangerous_rules` (it also re-runs after a hook's `updatedPermissions` are
    applied), and leaves the mode alone.
    """
    dangerous = find_dangerous_classifier_permissions(context.always_allow_rules)
    removable = tuple(rule for rule in dangerous if rule.source in PERMISSION_UPDATE_DESTINATIONS)
    for rule in dangerous:
        logger.info(
            "Ignoring dangerous permission %s from %s (bypasses the TDD phase gate)",
            permission_rule_value_to_string(rule.tool_name, rule.rule_content) if rule.rule_content
            else f"{rule.tool_name}(*)",
            rule.source,
        )
    return replace(
        context,
        always_allow_rules=tuple(rule for rule in context.always_allow_rules if rule not in removable),
        stripped_dangerous_rules=context.stripped_dangerous_rules + removable,
    )


__all__ = [
    "CROSS_PLATFORM_CODE_EXEC",
    "DANGEROUS_BASH_PATTERNS",
    "PERMISSION_UPDATE_DESTINATIONS",
    "find_dangerous_classifier_permissions",
    "is_dangerous_bash_permission",
    "is_dangerous_classifier_permission",
    "is_dangerous_task_permission",
    "strip_dangerous_permissions",
]
