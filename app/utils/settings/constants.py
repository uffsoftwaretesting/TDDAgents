"""
Settings-source constants.

Ported from `reference/claude-code/src/utils/settings/constants.ts` ->
`getSettingSourceDisplayNameLowercase`.
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
