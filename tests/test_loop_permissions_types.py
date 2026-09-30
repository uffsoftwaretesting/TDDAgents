"""
Tests for permission modes, behaviors, rules, and ladder cycling (Part C1).
"""

from __future__ import annotations

from app.loop.permissions.types import (
    EXTERNAL_PERMISSION_MODES,
    INTERNAL_PERMISSION_MODES,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
    cycle_permission_mode,
    get_next_permission_mode,
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
        assert ctx.is_bypass_permissions_mode_available is True
        assert ctx.additional_working_directories == ()


class TestModeLadderCycling:
    def test_mode_ladder_with_bypass_available(self):
        # Ladder: default -> acceptEdits -> plan -> bypassPermissions -> default
        assert get_next_permission_mode(PermissionMode.DEFAULT) == PermissionMode.ACCEPT_EDITS
        assert get_next_permission_mode(PermissionMode.ACCEPT_EDITS) == PermissionMode.PLAN
        assert get_next_permission_mode(PermissionMode.PLAN) == PermissionMode.BYPASS_PERMISSIONS
        assert (
            get_next_permission_mode(PermissionMode.PLAN, is_bypass_available=True)
            == PermissionMode.BYPASS_PERMISSIONS
        )
        assert (
            get_next_permission_mode(PermissionMode.BYPASS_PERMISSIONS, is_bypass_available=True)
            == PermissionMode.DEFAULT
        )
        assert get_next_permission_mode(PermissionMode.BYPASS_PERMISSIONS) == PermissionMode.DEFAULT

    def test_mode_ladder_without_bypass(self):
        # Without bypass: plan skips directly to default
        assert get_next_permission_mode(PermissionMode.PLAN, is_bypass_available=False) == PermissionMode.DEFAULT

    def test_mode_ladder_from_dont_ask_and_auto(self):
        assert get_next_permission_mode(PermissionMode.DONT_ASK) == PermissionMode.DEFAULT
        assert get_next_permission_mode(PermissionMode.AUTO) == PermissionMode.DEFAULT
        assert get_next_permission_mode("bubble") == PermissionMode.DEFAULT

    def test_cycle_permission_mode_helper(self):
        allow_rule = PermissionRule(tool_name="ReadFile")
        deny_rule = PermissionRule(tool_name="Dangerous", rule_behavior=PermissionBehavior.DENY)
        ask_rule = PermissionRule(tool_name="AskMe", rule_behavior=PermissionBehavior.ASK)

        ctx = ToolPermissionContext(
            mode=PermissionMode.DEFAULT,
            always_allow_rules=(allow_rule,),
            always_deny_rules=(deny_rule,),
            always_ask_rules=(ask_rule,),
            is_bypass_permissions_mode_available=False,
            additional_working_directories=("/extra/path",),
        )

        c1 = cycle_permission_mode(ctx)
        assert c1.mode == PermissionMode.ACCEPT_EDITS
        assert c1.always_allow_rules == (allow_rule,)
        assert c1.always_deny_rules == (deny_rule,)
        assert c1.always_ask_rules == (ask_rule,)
        assert c1.additional_working_directories == ("/extra/path",)
        assert c1.is_bypass_permissions_mode_available is False

        c2 = cycle_permission_mode(c1)
        assert c2.mode == PermissionMode.PLAN
        assert c2.always_allow_rules == (allow_rule,)
        assert c2.always_deny_rules == (deny_rule,)
        assert c2.always_ask_rules == (ask_rule,)

        c3 = cycle_permission_mode(c2)
        assert c3.mode == PermissionMode.DEFAULT  # bypass not available
        assert c3.always_allow_rules == (allow_rule,)
        assert c3.always_deny_rules == (deny_rule,)
        assert c3.always_ask_rules == (ask_rule,)

        # Cycle with bypass available: plan -> bypassPermissions
        ctx_bypass = ToolPermissionContext(
            mode=PermissionMode.PLAN,
            is_bypass_permissions_mode_available=True,
        )
        c_bypass = cycle_permission_mode(ctx_bypass)
        assert c_bypass.mode == PermissionMode.BYPASS_PERMISSIONS
