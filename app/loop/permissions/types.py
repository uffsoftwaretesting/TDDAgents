"""
Permission modes, behaviors, rules, and context definitions (Part C1).

Ported from:
- `reference/claude-code/src/types/permissions.ts`
- `reference/claude-code/src/utils/permissions/PermissionMode.ts`
- `reference/claude-code/src/utils/permissions/getNextPermissionMode.ts`
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """
    Outcome of checking tool permissions.
    """

    behavior: str = "allow"  # "allow" | "deny" | "ask" | "passthrough"
    updated_input: dict[str, Any] | None = None
    message: str = ""
    decision_reason: dict[str, Any] | None = None
    #: Upstream `isBashSecurityCheckForMisparsing` (`src/types/permissions.ts`): set on an
    #: 'ask' from a Bash security validator whose concern is a parser differential, so the
    #: Bash permission check blocks before any command splitting runs.
    is_bash_security_check_for_misparsing: bool = False


class PermissionMode(StrEnum):
    """
    Permission modes representing levels on the authorization ladder.

    Ported from `EXTERNAL_PERMISSION_MODES` and `INTERNAL_PERMISSION_MODES` in
    `reference/claude-code/src/types/permissions.ts`.
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"
    DONT_ASK = "dontAsk"
    AUTO = "auto"
    BUBBLE = "bubble"


EXTERNAL_PERMISSION_MODES: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS_PERMISSIONS,
    PermissionMode.DONT_ASK,
)

INTERNAL_PERMISSION_MODES: tuple[PermissionMode, ...] = (
    *EXTERNAL_PERMISSION_MODES,
    PermissionMode.AUTO,
    PermissionMode.BUBBLE,
)


class PermissionBehavior(StrEnum):
    """
    The outcome of a permission check or rule.

    Ported from `PermissionBehavior` in `reference/claude-code/src/types/permissions.ts`.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


class PermissionRuleSource(StrEnum):
    """
    Source from which a permission rule originated.

    Ported from `PermissionRuleSource` in `reference/claude-code/src/types/permissions.ts`.
    """

    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    LOCAL_SETTINGS = "localSettings"
    FLAG_SETTINGS = "flagSettings"
    POLICY_SETTINGS = "policySettings"
    CLI_ARG = "cliArg"
    COMMAND = "command"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """
    A single permission rule targeting a tool name and optional content pattern.
    """

    tool_name: str
    rule_behavior: PermissionBehavior = PermissionBehavior.ALLOW
    rule_content: str | None = None
    source: PermissionRuleSource = PermissionRuleSource.SESSION


@dataclass(frozen=True, slots=True)
class ToolPermissionContext:
    """
    The immutable permission context threaded into tool permission checks.

    Ported from `ToolPermissionContext` in `reference/claude-code/src/types/permissions.ts`.
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    always_allow_rules: tuple[PermissionRule, ...] = ()
    always_deny_rules: tuple[PermissionRule, ...] = ()
    always_ask_rules: tuple[PermissionRule, ...] = ()
    is_bypass_permissions_mode_available: bool = True
    additional_working_directories: tuple[str, ...] = ()
    #: Upstream `shouldAvoidPermissionPrompts`: no surface can show a prompt, so an
    #: unresolved 'ask' is denied (`hasPermissionsToUseTool`, background/headless agents).
    should_avoid_permission_prompts: bool = False


def get_next_permission_mode(
    current_mode: PermissionMode | str,
    is_bypass_available: bool = True,
) -> PermissionMode:
    """
    Determine the next permission mode when cycling through the ladder.

    Ladder cycle: default -> acceptEdits -> plan -> bypassPermissions (if available) -> default.
    Ported from `getNextPermissionMode` in `reference/claude-code/src/utils/permissions/getNextPermissionMode.ts`.
    """
    mode = PermissionMode(current_mode) if isinstance(current_mode, str) else current_mode

    if mode == PermissionMode.DEFAULT:
        return PermissionMode.ACCEPT_EDITS
    elif mode == PermissionMode.ACCEPT_EDITS:
        return PermissionMode.PLAN
    elif mode == PermissionMode.PLAN:
        if is_bypass_available:
            return PermissionMode.BYPASS_PERMISSIONS
        return PermissionMode.DEFAULT
    elif mode == PermissionMode.BYPASS_PERMISSIONS:
        return PermissionMode.DEFAULT
    elif mode == PermissionMode.DONT_ASK:
        return PermissionMode.DEFAULT
    else:
        # Fallback for auto, bubble, or other modes
        return PermissionMode.DEFAULT


def cycle_permission_mode(context: ToolPermissionContext) -> ToolPermissionContext:
    """
    Advance the permission mode of a ToolPermissionContext to the next rung on the ladder.
    """
    next_mode = get_next_permission_mode(
        context.mode,
        is_bypass_available=context.is_bypass_permissions_mode_available,
    )
    return replace(context, mode=next_mode)
