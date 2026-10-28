"""
Permission updates: the `updatedPermissions` a PermissionRequest hook returns.

Ported from claude-code v2.1.88:

* `src/utils/permissions/PermissionUpdateSchema.ts` -> `permissionUpdateSchema`,
  `permissionUpdateDestinationSchema` (validated by hand, not by Zod)
* `src/utils/permissions/PermissionUpdate.ts` -> `applyPermissionUpdate`,
  `applyPermissionUpdates`, `supportsPersistence`, `persistPermissionUpdate`,
  `persistPermissionUpdates`
* `src/utils/permissions/permissionsLoader.ts` -> `addPermissionRulesToSettings`
* `src/utils/settings/settings.ts` -> `updateSettingsForSource`

**In memory** an update is a pure function of the context, exactly as upstream: `setMode`
replaces the mode (unguarded, as v2.1.88 — the TDD phase tier sits above bypass anyway);
`addRules` appends; `replaceRules` overwrites one source's rules of one behavior;
`removeRules` filters by the serialized rule. Upstream keys rules by source; this context
holds flat tuples tagged with `source`, so "the rules of a destination" means the rules
whose `source` is that destination.

**On disk** only `userSettings`, `projectSettings` and `localSettings` persist; `session`
and `cliArg` are memory-only. `addRules` alone is refused under
`allowManagedPermissionRulesOnly`, and alone deduplicates. Errors are logged, never raised.

**TDD:** applying updates can re-introduce an interpreter allow (`Bash(python:*)`), which
the phase gate cannot see past. The caller re-runs `strip_dangerous_permissions` after every
apply (`utils/permissions/permissions.py`), so the TDD managed tier holds for the rest of the run.

Divergences: additional directories carry no source here (upstream records the
destination, used only for display); `updateSettingsForSource` does not add the local
settings file to `.gitignore` and has no settings cache to reset.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.types.permissions import (
    EXTERNAL_PERMISSION_MODES,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.permissions.permission_rule_parser import permission_rule_value_to_string
from app.utils.settings.settings import (
    SettingsLocations,
    parse_settings_file,
    permission_rule_value_from_string,
    settings_file_path_for_source,
    should_allow_managed_permission_rules_only,
)

logger = logging.getLogger("TDDOrchestrator.Permissions")

#: `permissionUpdateSchema`'s discriminator values.
UPDATE_TYPES: frozenset[str] = frozenset({
    "addRules", "replaceRules", "removeRules", "setMode", "addDirectories", "removeDirectories",
})
RULE_UPDATE_TYPES: frozenset[str] = frozenset({"addRules", "replaceRules", "removeRules"})
DIRECTORY_UPDATE_TYPES: frozenset[str] = frozenset({"addDirectories", "removeDirectories"})

#: `permissionUpdateDestinationSchema`.
UPDATE_DESTINATIONS: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.SESSION,
    PermissionRuleSource.CLI_ARG,
)

#: `supportsPersistence`.
PERSISTABLE_DESTINATIONS: frozenset[PermissionRuleSource] = frozenset({
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
})

RULE_BEHAVIORS: tuple[PermissionBehavior, ...] = (
    PermissionBehavior.ALLOW,
    PermissionBehavior.DENY,
    PermissionBehavior.ASK,
)


@dataclass(frozen=True, slots=True)
class PermissionUpdate:
    """One validated `PermissionUpdate`. Fields not used by `type` stay empty."""

    type: str
    destination: PermissionRuleSource
    rules: tuple[tuple[str, str | None], ...] = ()
    behavior: PermissionBehavior | None = None
    mode: PermissionMode | None = None
    directories: tuple[str, ...] = ()

    def rule_strings(self) -> list[str]:
        """`update.rules.map(permissionRuleValueToString)`."""
        return [permission_rule_value_to_string(name, content) for name, content in self.rules]


def create_read_rule_suggestion(
    dir_path: str, destination: PermissionRuleSource = PermissionRuleSource.SESSION
) -> PermissionUpdate | None:
    """`createReadRuleSuggestion`: `Read(//dir/**)` for an absolute dir; none for the root."""
    if dir_path == "/":
        return None
    content = f"/{dir_path}/**" if dir_path.startswith("/") else f"{dir_path}/**"
    return PermissionUpdate("addRules", destination, rules=(("Read", content),), behavior=PermissionBehavior.ALLOW)


def _parse_rule_value(raw: Any) -> tuple[str, str | None] | None:
    """`permissionRuleValueSchema`: `{toolName, ruleContent?}`."""
    if not isinstance(raw, Mapping):
        return None
    name = raw.get("toolName")
    content = raw.get("ruleContent")
    if not isinstance(name, str) or (content is not None and not isinstance(content, str)):
        return None
    return name, content


def parse_permission_update(raw: Any) -> PermissionUpdate | None:
    """One update, or `None` when it does not satisfy the schema."""
    if not isinstance(raw, Mapping) or raw.get("type") not in UPDATE_TYPES:
        return None
    update_type = str(raw["type"])
    try:
        destination = PermissionRuleSource(str(raw.get("destination")))
    except ValueError:
        return None
    if destination not in UPDATE_DESTINATIONS:
        return None
    if update_type in RULE_UPDATE_TYPES:
        rules_raw = raw.get("rules")
        try:
            behavior = PermissionBehavior(str(raw.get("behavior")))
        except ValueError:
            return None
        if not isinstance(rules_raw, list) or behavior not in RULE_BEHAVIORS:
            return None
        rules = [_parse_rule_value(r) for r in rules_raw]
        if any(r is None for r in rules):
            return None
        return PermissionUpdate(
            update_type, destination, rules=tuple(r for r in rules if r is not None), behavior=behavior
        )
    if update_type == "setMode":
        mode = raw.get("mode")
        if mode not in EXTERNAL_PERMISSION_MODES:
            return None
        return PermissionUpdate(update_type, destination, mode=PermissionMode(mode))
    directories = raw.get("directories")
    if not isinstance(directories, list) or not all(isinstance(d, str) for d in directories):
        return None
    return PermissionUpdate(update_type, destination, directories=tuple(directories))


def parse_permission_updates(raw: Any) -> tuple[PermissionUpdate, ...] | None:
    """
    `z.array(permissionUpdateSchema())`: every update valid, or `None` — upstream rejects the
    whole hook output when any entry fails the schema.
    """
    if not isinstance(raw, list):
        return None
    parsed = [parse_permission_update(item) for item in raw]
    if any(p is None for p in parsed):
        return None
    return tuple(p for p in parsed if p is not None)


# ── in memory ────────────────────────────────────────────────────────────────

def _rule_from_string(rule: str, behavior: PermissionBehavior, source: PermissionRuleSource) -> PermissionRule:
    """Upstream stores rule strings and parses them on lookup (`getAllowRules` & co.)."""
    name, content = permission_rule_value_from_string(rule)
    return PermissionRule(tool_name=name, rule_behavior=behavior, rule_content=content, source=source)


def _rules_of(context: ToolPermissionContext, behavior: PermissionBehavior) -> tuple[PermissionRule, ...]:
    if behavior == PermissionBehavior.ALLOW:
        return context.always_allow_rules
    if behavior == PermissionBehavior.DENY:
        return context.always_deny_rules
    return context.always_ask_rules


def _with_rules(
    context: ToolPermissionContext, behavior: PermissionBehavior, rules: tuple[PermissionRule, ...]
) -> ToolPermissionContext:
    """Upstream's `ruleKind` switch: allow, deny, else ask."""
    if behavior == PermissionBehavior.ALLOW:
        return replace(context, always_allow_rules=rules)
    if behavior == PermissionBehavior.DENY:
        return replace(context, always_deny_rules=rules)
    return replace(context, always_ask_rules=rules)


def apply_permission_update(context: ToolPermissionContext, update: PermissionUpdate) -> ToolPermissionContext:
    """`applyPermissionUpdate`."""
    if update.type == "setMode" and update.mode is not None:
        logger.debug("Applying permission update: Setting mode to '%s'", update.mode)
        return replace(context, mode=update.mode)
    if update.type in RULE_UPDATE_TYPES and update.behavior is not None:
        existing = _rules_of(context, update.behavior)
        strings = update.rule_strings()
        new_rules = tuple(_rule_from_string(r, update.behavior, update.destination) for r in strings)
        if update.type == "addRules":
            rules = existing + new_rules
        elif update.type == "replaceRules":
            rules = tuple(r for r in existing if r.source != update.destination) + new_rules
        else:
            removing = set(strings)
            rules = tuple(
                r for r in existing
                if r.source != update.destination
                or permission_rule_value_to_string(r.tool_name, r.rule_content) not in removing
            )
        logger.debug("Applying permission update: %s %s rule(s) for '%s'", update.type, len(strings),
                     update.destination)
        return _with_rules(context, update.behavior, rules)
    if update.type == "addDirectories":
        directories = list(context.additional_working_directories)
        for directory in update.directories:
            if directory not in directories:
                directories.append(directory)
        return replace(context, additional_working_directories=tuple(directories))
    if update.type == "removeDirectories":
        removing_dirs = set(update.directories)
        return replace(context, additional_working_directories=tuple(
            d for d in context.additional_working_directories if d not in removing_dirs))
    return context


def apply_permission_updates(
    context: ToolPermissionContext, updates: Sequence[PermissionUpdate]
) -> ToolPermissionContext:
    """`applyPermissionUpdates`: a left fold, so later updates see earlier ones."""
    for update in updates:
        context = apply_permission_update(context, update)
    return context


# ── on disk ──────────────────────────────────────────────────────────────────

def supports_persistence(destination: PermissionRuleSource) -> bool:
    """`supportsPersistence`."""
    return destination in PERSISTABLE_DESTINATIONS


def _merge_for_write(existing: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """`updateSettingsForSource`'s `mergeWith`: objects merge, arrays replace, `None` deletes."""
    merged: dict[str, Any] = dict(existing)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_for_write(merged[key], value)
        else:
            merged[key] = value
    return merged


def update_settings_for_source(
    source: PermissionRuleSource, locations: SettingsLocations, patch: Mapping[str, Any]
) -> str | None:
    """
    `updateSettingsForSource`: merge `patch` into one editable settings file. Returns an
    error message, or `None`. Policy and flag settings are read-only (a silent no-op); a
    file with a JSON syntax error is never overwritten.
    """
    if source not in PERSISTABLE_DESTINATIONS:
        return None
    path = settings_file_path_for_source(source, locations)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = parse_settings_file(path)
        if existing is None and path.exists():
            return f"Invalid JSON syntax in settings file at {path}"
        merged = _merge_for_write(existing or {}, patch)
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"Failed to write settings file at {path}: {exc}"
    return None


def _existing_permissions(source: PermissionRuleSource, locations: SettingsLocations) -> dict[str, Any]:
    path = settings_file_path_for_source(source, locations)
    data = parse_settings_file(path) if path is not None else None
    permissions = (data or {}).get("permissions")
    return dict(permissions) if isinstance(permissions, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _normalized(rule: str) -> str:
    """The parse -> serialize round-trip, so `Bash(*)` and legacy names compare equal."""
    return permission_rule_value_to_string(*permission_rule_value_from_string(rule))


def add_permission_rules_to_settings(
    rules: Sequence[str], behavior: PermissionBehavior, source: PermissionRuleSource, locations: SettingsLocations
) -> bool:
    """`addPermissionRulesToSettings`: dedup against what the file holds; refused when managed-only."""
    if should_allow_managed_permission_rules_only(locations):
        return False
    if not rules:
        return True
    permissions = _existing_permissions(source, locations)
    existing = _string_list(permissions.get(behavior.value))
    seen = {_normalized(r) for r in existing}
    new_rules = [r for r in rules if r not in seen]
    if not new_rules:
        return True
    error = update_settings_for_source(source, locations, {"permissions": {behavior.value: existing + new_rules}})
    if error is not None:
        logger.warning("Could not persist permission rules: %s", error)
        return False
    return True


def persist_permission_update(update: PermissionUpdate, locations: SettingsLocations) -> None:
    """`persistPermissionUpdate`: memory-only destinations return early."""
    if not supports_persistence(update.destination):
        return
    error: str | None = None
    if update.type == "addRules" and update.behavior is not None:
        add_permission_rules_to_settings(update.rule_strings(), update.behavior, update.destination, locations)
        return
    if update.type == "addDirectories":
        existing = _string_list(_existing_permissions(update.destination, locations).get("additionalDirectories"))
        additions = [d for d in update.directories if d not in existing]
        if additions:
            error = update_settings_for_source(
                update.destination, locations, {"permissions": {"additionalDirectories": existing + additions}}
            )
    elif update.type == "removeRules" and update.behavior is not None:
        existing = _string_list(_existing_permissions(update.destination, locations).get(update.behavior.value))
        removing = set(update.rule_strings())
        kept = [r for r in existing if _normalized(r) not in removing]
        error = update_settings_for_source(
            update.destination, locations, {"permissions": {update.behavior.value: kept}}
        )
    elif update.type == "removeDirectories":
        existing = _string_list(_existing_permissions(update.destination, locations).get("additionalDirectories"))
        kept = [d for d in existing if d not in set(update.directories)]
        error = update_settings_for_source(
            update.destination, locations, {"permissions": {"additionalDirectories": kept}}
        )
    elif update.type == "setMode" and update.mode is not None:
        error = update_settings_for_source(
            update.destination, locations, {"permissions": {"defaultMode": str(update.mode)}}
        )
    elif update.type == "replaceRules" and update.behavior is not None:
        error = update_settings_for_source(
            update.destination, locations, {"permissions": {update.behavior.value: update.rule_strings()}}
        )
    if error is not None:
        logger.warning("Could not persist permission update: %s", error)


def persist_permission_updates(updates: Sequence[PermissionUpdate], locations: SettingsLocations) -> None:
    """`persistPermissionUpdates`."""
    for update in updates:
        persist_permission_update(update, locations)


def settings_locations_for(context: ToolPermissionContext) -> SettingsLocations | None:
    """Where this run's settings live, when it was built from settings (`build_tool_permission_context`)."""
    locations = context.settings_locations
    return locations if isinstance(locations, SettingsLocations) else None


__all__ = [
    "DIRECTORY_UPDATE_TYPES",
    "PERSISTABLE_DESTINATIONS",
    "RULE_UPDATE_TYPES",
    "UPDATE_DESTINATIONS",
    "UPDATE_TYPES",
    "PermissionUpdate",
    "add_permission_rules_to_settings",
    "apply_permission_update",
    "apply_permission_updates",
    "create_read_rule_suggestion",
    "parse_permission_update",
    "parse_permission_updates",
    "persist_permission_update",
    "persist_permission_updates",
    "settings_locations_for",
    "supports_persistence",
    "update_settings_for_source",
]
