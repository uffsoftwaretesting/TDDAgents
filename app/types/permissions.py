"""
Permission modes, behaviors, rules, and context definitions (Part C1).

Ported from:
- `reference/claude-code/src/types/permissions.ts`
- `reference/claude-code/src/utils/permissions/PermissionMode.ts`
- `reference/claude-code/src/utils/permissions/getNextPermissionMode.ts`
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.utils.settings.settings import SettingsLocations


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
    #: Upstream `suggestions` (`PermissionUpdate[]`) on an 'ask' or 'passthrough': what the
    #: permission prompt offers as "don't ask again" (Phase 11). `None` means none were made,
    #: `()` that none may be (a command the security checks flagged).
    suggestions: tuple[Any, ...] | None = None


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

#: Upstream's runtime `INTERNAL_PERMISSION_MODES` / `PERMISSION_MODES`: the modes a user can
#: name in settings `defaultMode` or the run config — the external ones plus `auto`, which the
#: `TRANSCRIPT_CLASSIFIER` build adds (Phase 4b ports the classifier). `bubble` is internal only.
PERMISSION_MODES: tuple[PermissionMode, ...] = (*EXTERNAL_PERMISSION_MODES, PermissionMode.AUTO)


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
    #: Upstream `isBypassPermissionsModeAvailable`, false unless the run was started in
    #: `bypassPermissions` or with `allowDangerouslySkipPermissions`
    #: (`initializeToolPermissionContext`). It decides whether `plan` mode bypasses.
    is_bypass_permissions_mode_available: bool = False
    additional_working_directories: tuple[str, ...] = ()
    #: Upstream `shouldAvoidPermissionPrompts`: no surface can show a prompt, so an
    #: unresolved 'ask' is denied (`hasPermissionsToUseTool`, background/headless agents).
    should_avoid_permission_prompts: bool = False
    #: Where a `/`-rooted path pattern of each settings source is anchored — upstream's
    #: `rootPathForSource`. A source missing here is anchored at the working directory,
    #: which is upstream's answer for `projectSettings`, `localSettings`, `policySettings`,
    #: `cliArg`, `command` and `session` (all `getOriginalCwd()`).
    settings_roots: Mapping[PermissionRuleSource, str] = field(default_factory=dict)
    #: Upstream `strippedDangerousRules`: allow rules removed because they would let a
    #: command bypass the automated judge (`stripDangerousPermissionsForAutoMode`). Kept
    #: so the removal is visible after the fact, never consulted by the gate.
    stripped_dangerous_rules: tuple[PermissionRule, ...] = ()
    #: Where this run's settings files live, so `persistPermissionUpdates` can write the
    #: editable ones. Upstream finds them through process globals (cwd, home); `None` for a
    #: context not built from settings, which then persists nothing.
    settings_locations: SettingsLocations | None = None


#: `PERMISSION_MODE_CONFIG[mode].title`.
MODE_TITLES: Mapping[PermissionMode, str] = {
    PermissionMode.DEFAULT: "Default",
    PermissionMode.PLAN: "Plan Mode",
    PermissionMode.ACCEPT_EDITS: "Accept edits",
    PermissionMode.BYPASS_PERMISSIONS: "Bypass Permissions",
    PermissionMode.DONT_ASK: "Don't Ask",
    PermissionMode.AUTO: "Auto mode",
}


def permission_mode_title(mode: PermissionMode | str) -> str:
    """`permissionModeTitle`; an unknown mode is titled as `default`, as `getModeConfig` does."""
    try:
        return MODE_TITLES[PermissionMode(mode)]
    except (ValueError, KeyError):
        return MODE_TITLES[PermissionMode.DEFAULT]
