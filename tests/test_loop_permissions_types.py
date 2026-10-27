"""
Tests for permission modes, behaviors, rules, and ladder cycling (Part C1).
"""

from __future__ import annotations

from app.types.permissions import (
    EXTERNAL_PERMISSION_MODES,
    INTERNAL_PERMISSION_MODES,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)


class TestPermissionModesAndEnums:
    def test_permission_mode_values(self):
        assert PermissionMode.DEFAULT.value == "default"
        assert PermissionMode.ACCEPT_EDITS.value == "acceptEdits"
        assert PermissionMode.PLAN.value == "plan"
        assert PermissionMode.BYPASS_PERMISSIONS.value == "bypassPermissions"
        assert PermissionMode.DONT_ASK.value == "dontAsk"
        assert PermissionMode.AUTO.value == "auto"
        assert PermissionMode.BUBBLE.value == "bubble"

    def test_external_and_internal_mode_sets(self):
        assert len(EXTERNAL_PERMISSION_MODES) == 5
        assert PermissionMode.AUTO not in EXTERNAL_PERMISSION_MODES
        assert PermissionMode.BUBBLE not in EXTERNAL_PERMISSION_MODES

        assert len(INTERNAL_PERMISSION_MODES) == 7
        assert PermissionMode.AUTO in INTERNAL_PERMISSION_MODES
        assert PermissionMode.BUBBLE in INTERNAL_PERMISSION_MODES

    def test_permission_behavior_values(self):
        assert PermissionBehavior.ALLOW.value == "allow"
        assert PermissionBehavior.DENY.value == "deny"
        assert PermissionBehavior.ASK.value == "ask"
        assert PermissionBehavior.PASSTHROUGH.value == "passthrough"

    def test_permission_rule_source_values(self):
        assert PermissionRuleSource.USER_SETTINGS.value == "userSettings"
        assert PermissionRuleSource.PROJECT_SETTINGS.value == "projectSettings"
        assert PermissionRuleSource.LOCAL_SETTINGS.value == "localSettings"
        assert PermissionRuleSource.FLAG_SETTINGS.value == "flagSettings"
        assert PermissionRuleSource.POLICY_SETTINGS.value == "policySettings"
        assert PermissionRuleSource.CLI_ARG.value == "cliArg"
        assert PermissionRuleSource.COMMAND.value == "command"
        assert PermissionRuleSource.SESSION.value == "session"


class TestPermissionDataStructures:
    def test_permission_rule_defaults(self):
        rule = PermissionRule(tool_name="Bash")
        assert rule.tool_name == "Bash"
        assert rule.rule_behavior == PermissionBehavior.ALLOW
        assert rule.rule_content is None
        assert rule.source == PermissionRuleSource.SESSION

    def test_permission_rule_custom(self):
        rule = PermissionRule(
            tool_name="Bash",
            rule_behavior=PermissionBehavior.DENY,
            rule_content="rm -rf *",
            source=PermissionRuleSource.FLAG_SETTINGS,
        )
        assert rule.tool_name == "Bash"
        assert rule.rule_behavior == PermissionBehavior.DENY
        assert rule.rule_content == "rm -rf *"
        assert rule.source == PermissionRuleSource.FLAG_SETTINGS

    def test_tool_permission_context_defaults(self):
        ctx = ToolPermissionContext()
        assert ctx.mode == PermissionMode.DEFAULT
        assert ctx.always_allow_rules == ()
        assert ctx.always_deny_rules == ()
        assert ctx.always_ask_rules == ()
        # Upstream: bypass is available only when the run started in it (Phase 3).
        assert ctx.is_bypass_permissions_mode_available is False
        assert ctx.additional_working_directories == ()
        assert dict(ctx.settings_roots) == {}
        assert ctx.stripped_dangerous_rules == ()
