"""
The permission layer's user-facing wording, in one place.

Ported from claude-code v2.1.88:

* `src/utils/permissions/permissions.ts` -> `createPermissionRequestMessage`,
  `permissionRuleSourceDisplayString`
* `src/utils/permissions/permissionRuleParser.ts` -> `permissionRuleValueToString`
* `src/utils/settings/constants.ts` -> `getSettingSourceDisplayNameLowercase`
* `src/utils/permissions/PermissionMode.ts` -> `permissionModeTitle`
* `src/utils/messages.ts` -> `DENIAL_WORKAROUND_GUIDANCE`, `AUTO_REJECT_MESSAGE`,
  `DONT_ASK_REJECT_MESSAGE`

These strings are the model's only feedback about why a call did not run, so they are
ported verbatim, except that the agent is named where upstream names Claude / Claude Code.
The classifier, permission-prompt-tool and sandbox-override reasons are not ported: none of
those mechanisms exists in this loop.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.loop.permissions.types import PermissionMode, PermissionRule, PermissionRuleSource

#: `DENIAL_WORKAROUND_GUIDANCE` (`src/utils/messages.ts`).
DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally be used "
    "to accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around "
    "this denial in malicious ways, e.g. do not use your ability to run tests to execute non-test actions. "
    "You should only try to work around this restriction in reasonable ways that do not attempt to bypass "
    "the intent behind this denial. If you believe this capability is essential to complete the user's "
    "request, STOP and explain to the user what you were trying to do and why you need this permission. "
    "Let the user decide how to proceed."
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

#: `PERMISSION_MODE_CONFIG[mode].title`.
MODE_TITLES: Mapping[PermissionMode, str] = {
    PermissionMode.DEFAULT: "Default",
    PermissionMode.PLAN: "Plan Mode",
    PermissionMode.ACCEPT_EDITS: "Accept edits",
    PermissionMode.BYPASS_PERMISSIONS: "Bypass Permissions",
    PermissionMode.DONT_ASK: "Don't Ask",
    PermissionMode.AUTO: "Auto mode",
}


def auto_reject_message(tool_name: str) -> str:
    """`AUTO_REJECT_MESSAGE`."""
    return f"Permission to use {tool_name} has been denied. {DENIAL_WORKAROUND_GUIDANCE}"


def dont_ask_reject_message(tool_name: str) -> str:
    """`DONT_ASK_REJECT_MESSAGE`."""
    return (
        f"Permission to use {tool_name} has been denied because the agent is running in don't ask mode. "
        f"{DENIAL_WORKAROUND_GUIDANCE}"
    )


def escape_rule_content(content: str) -> str:
    """`escapeRuleContent`: backslashes first, then parentheses."""
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def permission_rule_value_to_string(tool_name: str, rule_content: str | None) -> str:
    """`permissionRuleValueToString`: `Bash` or `Bash(npm install)`, content re-escaped."""
    if not rule_content:
        return tool_name
    return f"{tool_name}({escape_rule_content(rule_content)})"


def permission_rule_source_display_string(source: PermissionRuleSource | str) -> str:
    """`permissionRuleSourceDisplayString`."""
    try:
        return SOURCE_DISPLAY_NAMES[PermissionRuleSource(source)]
    except ValueError:
        return str(source)


def permission_mode_title(mode: PermissionMode | str) -> str:
    """`permissionModeTitle`; an unknown mode is titled as `default`, as `getModeConfig` does."""
    try:
        return MODE_TITLES[PermissionMode(mode)]
    except (ValueError, KeyError):
        return MODE_TITLES[PermissionMode.DEFAULT]


def _subcommand_message(tool_name: str, reasons: Any) -> str:
    from app.loop.permissions.bash_commands import extract_output_redirections

    needs: list[str] = []
    if isinstance(reasons, Mapping):
        for cmd, result in reasons.items():
            if getattr(result, "behavior", None) in ("ask", "passthrough"):
                if tool_name == "Bash":
                    # Strip output redirections for display, so filenames do not read as commands.
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


def create_permission_request_message(
    tool_name: str,
    decision_reason: Mapping[str, Any] | None = None,
) -> str:
    """`createPermissionRequestMessage`: why a call needs approval, per reason type."""
    if decision_reason:
        reason_type = decision_reason.get("type")
        if reason_type == "hook":
            hook_name = decision_reason.get("hookName")
            reason = decision_reason.get("reason")
            if reason:
                return f"Hook '{hook_name}' blocked this action: {reason}"
            return f"Hook '{hook_name}' requires approval for this {tool_name} command"
        if reason_type == "rule":
            rule = decision_reason.get("rule")
            if isinstance(rule, PermissionRule):
                rule_string = permission_rule_value_to_string(rule.tool_name, rule.rule_content)
                source_string = permission_rule_source_display_string(rule.source)
                return (
                    f"Permission rule '{rule_string}' from {source_string} requires approval "
                    f"for this {tool_name} command"
                )
        if reason_type == "subcommandResults":
            return _subcommand_message(tool_name, decision_reason.get("reasons"))
        if reason_type in ("workingDir", "safetyCheck", "other", "asyncAgent"):
            return str(decision_reason.get("reason"))
        if reason_type == "mode":
            title = permission_mode_title(decision_reason.get("mode", PermissionMode.DEFAULT))
            return f"Current permission mode ({title}) requires approval for this {tool_name} command"
    return f"The agent requested permissions to use {tool_name}, but you haven't granted it yet."


__all__ = [
    "DENIAL_WORKAROUND_GUIDANCE",
    "MODE_TITLES",
    "SOURCE_DISPLAY_NAMES",
    "auto_reject_message",
    "create_permission_request_message",
    "dont_ask_reject_message",
    "escape_rule_content",
    "permission_mode_title",
    "permission_rule_source_display_string",
    "permission_rule_value_to_string",
]
