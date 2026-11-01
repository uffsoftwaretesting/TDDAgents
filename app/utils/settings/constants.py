"""
Settings-source constants.

Ported from `reference/claude-code/src/utils/settings/constants.ts` ->
`getSettingSourceDisplayNameLowercase`, `getSourceDisplayName`.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.types.permissions import (
    PermissionRuleSource,
)

#: `getSettingSourceDisplayNameLowercase`.
SOURCE_DISPLAY_NAMES: Mapping[PermissionRuleSource, str] = {
    PermissionRuleSource.USER_SETTINGS: "user settings",
    PermissionRuleSource.PROJECT_SETTINGS: "shared project settings",
    PermissionRuleSource.LOCAL_SETTINGS: "project local settings",
    PermissionRuleSource.FLAG_SETTINGS: "command line arguments",
    PermissionRuleSource.POLICY_SETTINGS: "enterprise managed settings",
    PermissionRuleSource.CLI_ARG: "CLI argument",
    PermissionRuleSource.COMMAND: "command configuration",
    PermissionRuleSource.SESSION: "current session",
}

#: `getSourceDisplayName`'s cases.
SOURCE_DISPLAY_NAMES_CAPITALIZED: Mapping[str, str] = {
    "userSettings": "User",
    "projectSettings": "Project",
    "localSettings": "Local",
    "flagSettings": "Flag",
    "policySettings": "Managed",
    "plugin": "Plugin",
    "built-in": "Built-in",
}


def get_source_display_name(source: str) -> str:
    """
    `getSourceDisplayName`. Upstream's switch has no default, so a source outside it (a bundled
    skill's `bundled`) renders as `undefined` in `/context`; here it renders as itself, as the
    command's agent table already does for an unknown source (`String(agent.source)`).
    """
    return SOURCE_DISPLAY_NAMES_CAPITALIZED.get(source, source)
