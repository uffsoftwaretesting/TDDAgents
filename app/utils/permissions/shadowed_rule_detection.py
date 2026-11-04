"""
Allow rules that can never apply: a tool-wide deny or ask rule for the same tool is checked first.

Ported from `reference/claude-code/src/utils/permissions/shadowedRuleDetection.ts` -> `ShadowType`,
`UnreachableRule`, `DetectUnreachableRulesOptions`, `isSharedSettingSource`, `generateFixSuggestion`,
`isAllowRuleShadowedByAskRule`, `isAllowRuleShadowedByDenyRule`, `detectUnreachableRules`. The rules
are the context's (`always_allow_rules`, `always_ask_rules`, `always_deny_rules`, upstream's
`getAllowRules` / `getAskRules` / `getDenyRules`), in their source order. `/doctor` reports them
(`app/utils/doctor_context_warnings.py`). This build has no Bash sandbox, so its callers pass
`sandbox_auto_allow_enabled=False`; the option is kept with upstream's meaning.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.types.permissions import PermissionRule, PermissionRuleSource, ToolPermissionContext
from app.utils.permissions.permissions import permission_rule_source_display_string

#: `BASH_TOOL_NAME`.
BASH_TOOL_NAME = "Bash"

ShadowType = Literal["ask", "deny"]


@dataclass(frozen=True, slots=True)
class UnreachableRule:
    """`UnreachableRule`: the allow rule, why it never applies, the rule in its way, and the fix."""

    rule: PermissionRule
    reason: str
    shadowed_by: PermissionRule
    shadow_type: ShadowType
    fix: str


def is_shared_setting_source(source: PermissionRuleSource | str) -> bool:
    """`isSharedSettingSource`: settings other people see (the project's, the policy's, a command's)."""
    return source in (PermissionRuleSource.PROJECT_SETTINGS, PermissionRuleSource.POLICY_SETTINGS,
                      PermissionRuleSource.COMMAND)


def generate_fix_suggestion(shadow_type: ShadowType, shadowing_rule: PermissionRule,
                            shadowed_rule: PermissionRule) -> str:
    """`generateFixSuggestion`."""
    shadowing_source = permission_rule_source_display_string(shadowing_rule.source)
    shadowed_source = permission_rule_source_display_string(shadowed_rule.source)
    return (f'Remove the "{shadowing_rule.tool_name}" {shadow_type} rule from {shadowing_source}, or remove the '
            f"specific allow rule from {shadowed_source}")


def _tool_wide(rules: Sequence[PermissionRule], tool_name: str) -> PermissionRule | None:
    return next((r for r in rules if r.tool_name == tool_name and r.rule_content is None), None)


def is_allow_rule_shadowed_by_ask_rule(allow_rule: PermissionRule, ask_rules: Sequence[PermissionRule], *,
                                       sandbox_auto_allow_enabled: bool) -> PermissionRule | None:
    """
    `isAllowRuleShadowedByAskRule`: the tool-wide ask rule a specific allow rule never gets past. With the
    sandbox auto-allowing Bash, a personal ask rule does not count (a shared one still does).
    """
    if allow_rule.rule_content is None:
        return None
    shadowing = _tool_wide(ask_rules, allow_rule.tool_name)
    if shadowing is None:
        return None
    if (allow_rule.tool_name == BASH_TOOL_NAME and sandbox_auto_allow_enabled
            and not is_shared_setting_source(shadowing.source)):
        return None
    return shadowing


def is_allow_rule_shadowed_by_deny_rule(allow_rule: PermissionRule,
                                        deny_rules: Sequence[PermissionRule]) -> PermissionRule | None:
    """`isAllowRuleShadowedByDenyRule`: the tool-wide deny rule that blocks a specific allow rule."""
    if allow_rule.rule_content is None:
        return None
    return _tool_wide(deny_rules, allow_rule.tool_name)


def detect_unreachable_rules(context: ToolPermissionContext, *,
                             sandbox_auto_allow_enabled: bool = False) -> list[UnreachableRule]:
    """`detectUnreachableRules`: each allow rule shadowed by a deny rule, else by an ask rule."""
    unreachable: list[UnreachableRule] = []
    for allow_rule in context.always_allow_rules:
        deny = is_allow_rule_shadowed_by_deny_rule(allow_rule, context.always_deny_rules)
        if deny is not None:
            unreachable.append(UnreachableRule(
                rule=allow_rule,
                reason=f'Blocked by "{deny.tool_name}" deny rule (from '
                       f"{permission_rule_source_display_string(deny.source)})",
                shadowed_by=deny, shadow_type="deny", fix=generate_fix_suggestion("deny", deny, allow_rule)))
            continue
        ask = is_allow_rule_shadowed_by_ask_rule(allow_rule, context.always_ask_rules,
                                                 sandbox_auto_allow_enabled=sandbox_auto_allow_enabled)
        if ask is not None:
            unreachable.append(UnreachableRule(
                rule=allow_rule,
                reason=f'Shadowed by "{ask.tool_name}" ask rule (from '
                       f"{permission_rule_source_display_string(ask.source)})",
                shadowed_by=ask, shadow_type="ask", fix=generate_fix_suggestion("ask", ask, allow_rule)))
    return unreachable


__all__ = [
    "BASH_TOOL_NAME",
    "ShadowType",
    "UnreachableRule",
    "detect_unreachable_rules",
    "generate_fix_suggestion",
    "is_allow_rule_shadowed_by_ask_rule",
    "is_allow_rule_shadowed_by_deny_rule",
    "is_shared_setting_source",
]
