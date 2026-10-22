"""
Settings sources, permission rules from settings, and the run's initial permission context.

Ported from claude-code v2.1.88:

* `src/utils/settings/constants.ts` -> `SETTING_SOURCES`, `getEnabledSettingSources`
* `src/utils/settings/managedPath.ts` -> `getManagedFilePath`, `getManagedSettingsDropInDir`
* `src/utils/settings/settings.ts` -> `getSettingsFilePathForSource`,
  `getSettingsRootPathForSource`, `loadManagedFileSettings`, `loadSettingsFromDisk`,
  `settingsMergeCustomizer`
* `src/utils/permissions/permissionRuleParser.ts` -> `permissionRuleValueFromString`,
  `unescapeRuleContent`, `normalizeLegacyToolName`
* `src/utils/permissions/permissionsLoader.ts` -> `shouldAllowManagedPermissionRulesOnly`,
  `settingsJsonToRules`, `loadAllPermissionRulesFromDisk`
* `src/utils/permissions/permissionSetup.ts` -> `parseToolListFromCLI`,
  `initialPermissionModeFromCLI`, `initializeToolPermissionContext`
* `src/utils/permissions/PermissionMode.ts` -> `permissionModeFromString`

The sources, in upstream's load order, with TDDAgents' names (`.tddagents/`, `/etc/tddagents`):

    userSettings      ~/.tddagents/settings.json
    projectSettings   <project>/.tddagents/settings.json
    localSettings     <project>/.tddagents/settings.local.json
    flagSettings      the file named by the run (upstream `--settings`)
    policySettings    /etc/tddagents/managed-settings.json + managed-settings.d/*.json

Rules from every source contribute; deny beats ask beats allow at match time, not load
time. With `allowManagedPermissionRulesOnly` in policy settings, only policy rules load.
Non-rule settings (`defaultMode`, `disableBypassPermissionsMode`, `additionalDirectories`)
come from the deep merge of all sources, later sources winning — `loadSettingsFromDisk`.

Divergences: policy settings have one tier here, the file plus drop-ins; upstream's remote,
MDM and HKCU tiers have no equivalent. Settings are read leniently (malformed files and
entries are logged and skipped) rather than validated against upstream's Zod schema.
`--base-tools` and the PWD-symlink working directory are not ported: no CLI exists yet.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.loop.permissions.messages import escape_rule_content
from app.loop.permissions.types import (
    PERMISSION_MODES,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

logger = logging.getLogger("TDDOrchestrator.Permissions")

#: The product's config directory, upstream's `.claude`.
SETTINGS_DIR = ".tddagents"
SETTINGS_FILENAME = "settings.json"
LOCAL_SETTINGS_FILENAME = "settings.local.json"
#: `getManagedFilePath` on Linux, renamed. The memory loader's `MANAGED_DIR` is the same.
MANAGED_DIR = "/etc/tddagents"
MANAGED_SETTINGS_FILENAME = "managed-settings.json"
MANAGED_DROP_IN_DIRNAME = "managed-settings.d"

#: `SUPPORTED_RULE_BEHAVIORS`, in upstream's load order.
SUPPORTED_RULE_BEHAVIORS: tuple[PermissionBehavior, ...] = (
    PermissionBehavior.ALLOW,
    PermissionBehavior.DENY,
    PermissionBehavior.ASK,
)

#: `getEnabledSettingSources()` — every source, in load and merge order.
SETTING_SOURCES: tuple[PermissionRuleSource, ...] = (
    PermissionRuleSource.USER_SETTINGS,
    PermissionRuleSource.PROJECT_SETTINGS,
    PermissionRuleSource.LOCAL_SETTINGS,
    PermissionRuleSource.FLAG_SETTINGS,
    PermissionRuleSource.POLICY_SETTINGS,
)

#: The three editable file scopes, in order (also the hook-settings scopes).
SETTINGS_SOURCES: tuple[PermissionRuleSource, ...] = SETTING_SOURCES[:3]

#: `LEGACY_TOOL_NAME_ALIASES` (the external build: no Brief alias).
LEGACY_TOOL_NAME_ALIASES: dict[str, str] = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
}


@dataclass(frozen=True, slots=True)
class SettingsLocations:
    """
    Where each settings source lives for one run.

    `home` is the base of the user scope (`~`); `flag_settings_path` is upstream's
    `--settings` file, `None` when the run names none; `managed_dir` is the policy root.
    """

    project_root: Path
    home: Path | None = None
    flag_settings_path: Path | None = None
    managed_dir: Path = Path(MANAGED_DIR)

    @property
    def user_config_dir(self) -> Path:
        base = self.home if self.home is not None else Path.home()
        return base / SETTINGS_DIR


def normalize_legacy_tool_name(name: str) -> str:
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


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


# ── files ─────────────────────────────────────────────────────────────────────

def settings_paths(project_root: Path, home: Path | None = None) -> list[Path]:
    """The three editable scopes' files, in merge order. Existence is not checked here."""
    locations = SettingsLocations(project_root=Path(project_root), home=home)
    return [
        locations.user_config_dir / SETTINGS_FILENAME,
        locations.project_root / SETTINGS_DIR / SETTINGS_FILENAME,
        locations.project_root / SETTINGS_DIR / LOCAL_SETTINGS_FILENAME,
    ]


def settings_file_path_for_source(source: PermissionRuleSource, locations: SettingsLocations) -> Path | None:
    """`getSettingsFilePathForSource`."""
    if source == PermissionRuleSource.USER_SETTINGS:
        return locations.user_config_dir / SETTINGS_FILENAME
    if source == PermissionRuleSource.PROJECT_SETTINGS:
        return locations.project_root / SETTINGS_DIR / SETTINGS_FILENAME
    if source == PermissionRuleSource.LOCAL_SETTINGS:
        return locations.project_root / SETTINGS_DIR / LOCAL_SETTINGS_FILENAME
    if source == PermissionRuleSource.POLICY_SETTINGS:
        return locations.managed_dir / MANAGED_SETTINGS_FILENAME
    if source == PermissionRuleSource.FLAG_SETTINGS:
        return locations.flag_settings_path
    return None


def settings_root_path_for_source(source: PermissionRuleSource, locations: SettingsLocations) -> Path | None:
    """
    `getSettingsRootPathForSource`, for anchoring `/`-rooted path patterns.

    `None` means the working directory, upstream's `getOriginalCwd()`. In TDDAgents the
    agent's working directory is the workspace, not the repository holding the project
    settings, and the workspace is where upstream's project-relative patterns point.
    """
    if source == PermissionRuleSource.USER_SETTINGS:
        return locations.user_config_dir
    if source == PermissionRuleSource.FLAG_SETTINGS and locations.flag_settings_path is not None:
        return locations.flag_settings_path.resolve().parent
    return None


def parse_settings_file(path: Path) -> dict[str, Any] | None:
    """
    `parseSettingsFile`, leniently: a missing file is `None`, an empty one `{}`, a
    malformed one is logged and `None`. A non-object document is malformed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError as exc:
        logger.warning("Ignoring malformed settings at %s: %s", path, exc)
        return None
    if text.strip() == "":
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed settings at %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Ignoring malformed settings at %s: not a JSON object", path)
        return None
    if "hooks" in data:
        # `parseSettingsFileUncached`: a document failing `SettingsSchema` yields
        # `settings: null` — the whole file (its hooks, rules and `defaultMode`) is ignored.
        from app.loop.hooks.schemas import hooks_schema_errors

        errors = hooks_schema_errors(data["hooks"])
        if errors:
            logger.warning("Invalid settings at %s, file ignored:\n%s", path, "\n".join(errors))
            return None
    return data


def merge_settings(target: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    """
    lodash `mergeWith(target, source, settingsMergeCustomizer)`, without mutation.

    Objects merge recursively, arrays concatenate and deduplicate (`mergeArrays`), and any
    other value from `source` replaces the target's.
    """
    merged: dict[str, Any] = dict(target)
    for key, value in source.items():
        existing = merged.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            combined: list[Any] = []
            for item in [*existing, *value]:
                if item not in combined:
                    combined.append(item)
            merged[key] = combined
        elif isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_settings(existing, value)
        else:
            merged[key] = value
    return merged


def load_managed_file_settings(managed_dir: Path) -> dict[str, Any] | None:
    """
    `loadManagedFileSettings`: the base file, then every `*.json` drop-in (not dot-files)
    in name order, each merged on top. `None` when nothing with content was found.
    """
    merged: dict[str, Any] = {}
    found = False
    base = parse_settings_file(managed_dir / MANAGED_SETTINGS_FILENAME)
    if base:
        merged = merge_settings(merged, base)
        found = True
    drop_in_dir = managed_dir / MANAGED_DROP_IN_DIRNAME
    try:
        names = sorted(
            entry.name
            for entry in drop_in_dir.iterdir()
            if entry.name.endswith(".json") and not entry.name.startswith(".") and entry.is_file()
        )
    except OSError:
        names = []
    for name in names:
        settings = parse_settings_file(drop_in_dir / name)
        if settings:
            merged = merge_settings(merged, settings)
            found = True
    return merged if found else None


def get_settings_for_source(source: PermissionRuleSource, locations: SettingsLocations) -> dict[str, Any] | None:
    """`getSettingsForSource`: one source's document, or `None`."""
    if source == PermissionRuleSource.POLICY_SETTINGS:
        return load_managed_file_settings(locations.managed_dir)
    path = settings_file_path_for_source(source, locations)
    if path is None:
        return None
    return parse_settings_file(path)


def load_settings(locations: SettingsLocations) -> dict[str, Any]:
    """`loadSettingsFromDisk`: every source merged in order, each file read once."""
    merged: dict[str, Any] = {}
    seen: set[Path] = set()
    for source in SETTING_SOURCES:
        if source != PermissionRuleSource.POLICY_SETTINGS:
            path = settings_file_path_for_source(source, locations)
            if path is None:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
        settings = get_settings_for_source(source, locations)
        if settings:
            merged = merge_settings(merged, settings)
    return merged


# ── rules ─────────────────────────────────────────────────────────────────────

def should_allow_managed_permission_rules_only(locations: SettingsLocations) -> bool:
    """`shouldAllowManagedPermissionRulesOnly`."""
    policy = get_settings_for_source(PermissionRuleSource.POLICY_SETTINGS, locations) or {}
    return policy.get("allowManagedPermissionRulesOnly") is True


def get_permission_rules_for_source(source: PermissionRuleSource, locations: SettingsLocations) -> list[PermissionRule]:
    """`getPermissionRulesForSource`."""
    origin = settings_file_path_for_source(source, locations)
    return settings_json_to_rules(get_settings_for_source(source, locations), source, str(origin))


def load_all_permission_rules_from_disk(locations: SettingsLocations) -> list[PermissionRule]:
    """`loadAllPermissionRulesFromDisk`."""
    if should_allow_managed_permission_rules_only(locations):
        return get_permission_rules_for_source(PermissionRuleSource.POLICY_SETTINGS, locations)
    rules: list[PermissionRule] = []
    for source in SETTING_SOURCES:
        rules.extend(get_permission_rules_for_source(source, locations))
    return rules


def load_permission_rules(project_root: Path | str, home: Path | None = None) -> list[PermissionRule]:
    """The editable scopes' rules only (user, project, local) — no flag or policy file."""
    locations = SettingsLocations(project_root=Path(project_root), home=home, managed_dir=Path(os.devnull))
    rules: list[PermissionRule] = []
    for source in SETTINGS_SOURCES:
        rules.extend(get_permission_rules_for_source(source, locations))
    return rules


def parse_tool_list_from_cli(tools: Iterable[str]) -> list[str]:
    """`parseToolListFromCLI`: split on commas and spaces outside parentheses."""
    result: list[str] = []
    for tool_string in tools:
        if not tool_string:
            continue
        current = ""
        in_parens = False
        for char in tool_string:
            if char == "(":
                in_parens = True
                current += char
            elif char == ")":
                in_parens = False
                current += char
            elif char in (",", " ") and not in_parens:
                if current.strip():
                    result.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            result.append(current.strip())
    return result


# ── mode and context ──────────────────────────────────────────────────────────

def permission_mode_from_string(value: str) -> PermissionMode:
    """`permissionModeFromString` over `PERMISSION_MODES`: an unknown mode is `default`."""
    return PermissionMode(value) if value in PERMISSION_MODES else PermissionMode.DEFAULT


def _bypass_disabled(settings: Mapping[str, Any]) -> bool:
    permissions = settings.get("permissions")
    return isinstance(permissions, dict) and permissions.get("disableBypassPermissionsMode") == "disable"


def initial_permission_mode_from_cli(
    *,
    permission_mode_cli: str | None,
    dangerously_skip_permissions: bool,
    settings: Mapping[str, Any],
) -> tuple[PermissionMode, str | None]:
    """
    `initialPermissionModeFromCLI`: the first of `--dangerously-skip-permissions`, the CLI
    mode, then `permissions.defaultMode` from settings, skipping `bypassPermissions` when
    settings disable it. Returns the mode and an optional notification.
    """
    disabled = _bypass_disabled(settings)
    ordered: list[PermissionMode] = []
    if dangerously_skip_permissions:
        ordered.append(PermissionMode.BYPASS_PERMISSIONS)
    if permission_mode_cli:
        ordered.append(permission_mode_from_string(permission_mode_cli))
    permissions = settings.get("permissions")
    default_mode = permissions.get("defaultMode") if isinstance(permissions, dict) else None
    if isinstance(default_mode, str) and default_mode:
        ordered.append(permission_mode_from_string(default_mode))
    notification: str | None = None
    for mode in ordered:
        if mode == PermissionMode.BYPASS_PERMISSIONS and disabled:
            logger.warning("bypassPermissions mode is disabled by settings")
            notification = "Bypass permissions mode was disabled by settings"
            continue
        return mode, notification
    return PermissionMode.DEFAULT, notification


def validate_additional_directory(directory: str, working_dir: Path | None) -> tuple[str | None, str | None]:
    """
    `validateDirectoryForWorkspace`, reduced to its outcomes: `(absolute_path, None)` to
    add, `(None, warning)` for a configuration mistake, `(None, None)` to skip silently (the
    directory is gone, or already inside the working directory).
    """
    absolute = Path(os.path.expanduser(directory))
    if not absolute.is_absolute() and working_dir is not None:
        absolute = working_dir / absolute
    absolute = Path(os.path.normpath(absolute))
    if not absolute.exists():
        return None, None
    if not absolute.is_dir():
        return None, f"{directory} is not a directory."
    if working_dir is not None:
        resolved_work = working_dir.resolve()
        resolved = absolute.resolve()
        if resolved == resolved_work or resolved_work in resolved.parents:
            return None, None
    return str(absolute), None


def initialize_tool_permission_context(
    locations: SettingsLocations,
    *,
    permission_mode: PermissionMode,
    allow_dangerously_skip_permissions: bool = False,
    allowed_tools_cli: Sequence[str] = (),
    disallowed_tools_cli: Sequence[str] = (),
    add_dirs: Sequence[str] = (),
    working_dir: Path | None = None,
    should_avoid_permission_prompts: bool = True,
) -> tuple[ToolPermissionContext, list[str]]:
    """
    `initializeToolPermissionContext`: CLI rules, every settings rule, the bypass
    availability, and the additional working directories. Returns the context and any
    warnings.

    `should_avoid_permission_prompts` defaults to True because this loop has no surface to
    show a prompt on: an unresolved 'ask' is denied, as upstream does for headless agents.
    """
    settings = load_settings(locations)
    is_bypass_available = (
        permission_mode == PermissionMode.BYPASS_PERMISSIONS or allow_dangerously_skip_permissions
    ) and not _bypass_disabled(settings)

    cli_rules: list[PermissionRule] = []
    for spec in parse_tool_list_from_cli(allowed_tools_cli):
        name, content = permission_rule_value_from_string(spec)
        cli_rules.append(PermissionRule(name, PermissionBehavior.ALLOW, content, PermissionRuleSource.CLI_ARG))
    for spec in parse_tool_list_from_cli(disallowed_tools_cli):
        name, content = permission_rule_value_from_string(spec)
        cli_rules.append(PermissionRule(name, PermissionBehavior.DENY, content, PermissionRuleSource.CLI_ARG))
    rules = [*load_all_permission_rules_from_disk(locations), *cli_rules]

    warnings: list[str] = []
    directories: list[str] = []
    permissions = settings.get("permissions")
    configured = permissions.get("additionalDirectories") if isinstance(permissions, dict) else None
    candidates = [d for d in (configured if isinstance(configured, list) else []) if isinstance(d, str)]
    for directory in [*candidates, *add_dirs]:
        added, warning = validate_additional_directory(directory, working_dir)
        if added is not None and added not in directories:
            directories.append(added)
        if warning is not None:
            warnings.append(warning)

    roots: dict[PermissionRuleSource, str] = {}
    for source in SETTING_SOURCES:
        root = settings_root_path_for_source(source, locations)
        if root is not None:
            roots[source] = str(root)

    context = ToolPermissionContext(
        mode=permission_mode,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == PermissionBehavior.ASK),
        is_bypass_permissions_mode_available=is_bypass_available,
        additional_working_directories=tuple(directories),
        should_avoid_permission_prompts=should_avoid_permission_prompts,
        settings_roots=roots,
        settings_locations=locations,
    )
    return context, warnings


def build_tool_permission_context(
    project_root: Path | str,
    home: Path | None = None,
    *,
    flag_settings_path: Path | str | None = None,
    managed_dir: Path | str = MANAGED_DIR,
    permission_mode_cli: str | None = None,
    dangerously_skip_permissions: bool = False,
    allowed_tools_cli: Sequence[str] = (),
    disallowed_tools_cli: Sequence[str] = (),
    add_dirs: Sequence[str] = (),
    working_dir: Path | str | None = None,
    should_avoid_permission_prompts: bool = True,
) -> ToolPermissionContext:
    """
    The run's permission context, the way upstream's startup builds it, plus the TDD tier.

    1. `initialPermissionModeFromCLI` resolves the mode (CLI, then settings `defaultMode`).
    2. `initializeToolPermissionContext` loads every source's rules.
    3. **TDD managed tier:** `strip_dangerous_permissions` removes interpreter and Agent
       allow rules, which the phase gate could not see past (`app/loop/permissions/dangerous.py`).
       The phase rules themselves live in code, keyed on the ledger, and are evaluated by
       the gate before any rule from this context — no settings allow can reach them.
    """
    from app.loop.permissions.dangerous import strip_dangerous_permissions

    locations = SettingsLocations(
        project_root=Path(project_root),
        home=home,
        flag_settings_path=Path(flag_settings_path) if flag_settings_path is not None else None,
        managed_dir=Path(managed_dir),
    )
    mode, notification = initial_permission_mode_from_cli(
        permission_mode_cli=permission_mode_cli,
        dangerously_skip_permissions=dangerously_skip_permissions,
        settings=load_settings(locations),
    )
    if notification:
        logger.warning(notification)
    context, warnings = initialize_tool_permission_context(
        locations,
        permission_mode=mode,
        allow_dangerously_skip_permissions=dangerously_skip_permissions,
        allowed_tools_cli=allowed_tools_cli,
        disallowed_tools_cli=disallowed_tools_cli,
        add_dirs=add_dirs,
        working_dir=Path(working_dir) if working_dir is not None else None,
        should_avoid_permission_prompts=should_avoid_permission_prompts,
    )
    for warning in warnings:
        logger.warning(warning)
    return strip_dangerous_permissions(context)


__all__ = [
    "LEGACY_TOOL_NAME_ALIASES",
    "LOCAL_SETTINGS_FILENAME",
    "MANAGED_DIR",
    "MANAGED_DROP_IN_DIRNAME",
    "MANAGED_SETTINGS_FILENAME",
    "SETTINGS_DIR",
    "SETTINGS_FILENAME",
    "SETTINGS_SOURCES",
    "SETTING_SOURCES",
    "SUPPORTED_RULE_BEHAVIORS",
    "SettingsLocations",
    "build_tool_permission_context",
    "escape_rule_content",
    "get_permission_rules_for_source",
    "get_settings_for_source",
    "initial_permission_mode_from_cli",
    "initialize_tool_permission_context",
    "load_all_permission_rules_from_disk",
    "load_managed_file_settings",
    "load_permission_rules",
    "load_settings",
    "merge_settings",
    "normalize_legacy_tool_name",
    "parse_settings_file",
    "parse_tool_list_from_cli",
    "permission_mode_from_string",
    "permission_rule_value_from_string",
    "settings_file_path_for_source",
    "settings_json_to_rules",
    "settings_paths",
    "settings_root_path_for_source",
    "should_allow_managed_permission_rules_only",
    "unescape_rule_content",
    "validate_additional_directory",
]
