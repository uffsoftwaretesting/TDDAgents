"""
Permission rules from settings files: `permissions.allow` / `.deny` / `.ask`.

Ported from:

- `reference/claude-code/src/utils/permissions/permissionRuleParser.ts` ->
  `permissionRuleValueFromString`, `escapeRuleContent`, `unescapeRuleContent`,
  `normalizeLegacyToolName`.
- `reference/claude-code/src/utils/permissions/permissionsLoader.ts` ->
  `settingsJsonToRules`, `loadAllPermissionRulesFromDisk` (the user/project/local subset).

The files are the three hook-settings scopes of `app/hooks/config.py`, read in the same
order and mapped to upstream's rule sources:

    ~/.tddagents/settings.json              userSettings
    <repo>/.tddagents/settings.json         projectSettings
    <repo>/.tddagents/settings.local.json   localSettings

Every scope contributes; nothing overrides. As upstream, deny beats ask beats allow at
match time, not at load time. A malformed file or entry is logged and skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.hooks.config import settings_paths
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

logger = logging.getLogger("TDDOrchestrator.Permissions")

#: `SUPPORTED_RULE_BEHAVIORS`, in upstream's load order.
SUPPORTED_RULE_BEHAVIORS: tuple[PermissionBehavior, ...] = (
    PermissionBehavior.ALLOW,
    PermissionBehavior.DENY,
    PermissionBehavior.ASK,
)

#: The scope order of `settings_paths`, as upstream rule sources.
SETTINGS_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
)

#: `LEGACY_TOOL_NAME_ALIASES` (the external build: no Brief alias).
LEGACY_TOOL_NAME_ALIASES: dict[str, str] = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
}


def normalize_legacy_tool_name(name: str) -> str:
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


def escape_rule_content(content: str) -> str:
    """`escapeRuleContent`: backslashes first, then parentheses."""
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def unescape_rule_content(content: str) -> str:
    """`unescapeRuleContent`: parentheses first, then backslashes."""
    return content.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def _escaped(s: str, i: int) -> bool:
    backslashes = 0
    j = i - 1
    while j >= 0 and s[j] == "\\":
        backslashes += 1
        j -= 1
    return backslashes % 2 == 1


def _find_first_unescaped(s: str, char: str) -> int:
    for i, ch in enumerate(s):
        if ch == char and not _escaped(s, i):
            return i
    return -1


def _find_last_unescaped(s: str, char: str) -> int:
    for i in range(len(s) - 1, -1, -1):
        if s[i] == char and not _escaped(s, i):
            return i
    return -1


def permission_rule_value_from_string(rule_string: str) -> tuple[str, str | None]:
    """
    `permissionRuleValueFromString`: `Bash(npm install)` -> ("Bash", "npm install").

    A missing, malformed, empty or `*` content makes it a tool-wide rule.
    """
    open_idx = _find_first_unescaped(rule_string, "(")
    if open_idx == -1:
        return normalize_legacy_tool_name(rule_string), None
    close_idx = _find_last_unescaped(rule_string, ")")
    if close_idx == -1 or close_idx <= open_idx:
        return normalize_legacy_tool_name(rule_string), None
    if close_idx != len(rule_string) - 1:
        return normalize_legacy_tool_name(rule_string), None
    tool_name = rule_string[:open_idx]
    raw_content = rule_string[open_idx + 1:close_idx]
    if not tool_name:
        return normalize_legacy_tool_name(rule_string), None
    if raw_content in ("", "*"):
        return normalize_legacy_tool_name(tool_name), None
    return normalize_legacy_tool_name(tool_name), unescape_rule_content(raw_content)


def settings_json_to_rules(data: Any, source: PermissionRuleSource, origin: str = "") -> list[PermissionRule]:
    """`settingsJsonToRules`: allow, then deny, then ask, each in file order."""
    if not isinstance(data, dict):
        return []
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return []
    rules: list[PermissionRule] = []
    for behavior in SUPPORTED_RULE_BEHAVIORS:
        entries = permissions.get(behavior.value)
        if entries is None:
            continue
        if not isinstance(entries, list):
            logger.warning("Ignoring non-list permissions.%s in %s.", behavior.value, origin)
            continue
        for entry in entries:
            if not isinstance(entry, str):
                logger.warning("Ignoring non-string permissions.%s entry in %s.", behavior.value, origin)
                continue
            tool_name, content = permission_rule_value_from_string(entry)
            rules.append(
                PermissionRule(tool_name=tool_name, rule_behavior=behavior, rule_content=content, source=source)
            )
    return rules


def _read_settings(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError as exc:
        logger.warning("Ignoring malformed settings at %s: %s", path, exc)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed settings at %s: %s", path, exc)
        return None


def load_permission_rules(project_root: Path | str, home: Path | None = None) -> list[PermissionRule]:
    """`loadAllPermissionRulesFromDisk` over the user, project and local scopes."""
    rules: list[PermissionRule] = []
    for source, path in zip(SETTINGS_SOURCES, settings_paths(Path(project_root), home)):
        if path.is_file():
            rules.extend(settings_json_to_rules(_read_settings(path), source, str(path)))
    return rules


def build_tool_permission_context(
    project_root: Path | str,
    home: Path | None = None,
    *,
    mode: PermissionMode = PermissionMode.DEFAULT,
    should_avoid_permission_prompts: bool = True,
) -> ToolPermissionContext:
    """
    A `ToolPermissionContext` whose rules come from the settings scopes.

    `should_avoid_permission_prompts` defaults to True because this loop has no surface to
    show a prompt on: an unresolved 'ask' is denied, as upstream does for background agents.
    """
    rules = load_permission_rules(project_root, home)
    return ToolPermissionContext(
        mode=mode,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ASK),
        should_avoid_permission_prompts=should_avoid_permission_prompts,
    )


__all__ = [
    "LEGACY_TOOL_NAME_ALIASES",
    "SETTINGS_SOURCES",
    "SUPPORTED_RULE_BEHAVIORS",
    "build_tool_permission_context",
    "escape_rule_content",
    "load_permission_rules",
    "normalize_legacy_tool_name",
    "permission_rule_value_from_string",
    "settings_json_to_rules",
    "unescape_rule_content",
]
