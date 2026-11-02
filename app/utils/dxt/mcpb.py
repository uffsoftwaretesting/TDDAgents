"""
The MCPB (formerly DXT) bundle format: the manifest schema, and the MCP server configuration a
manifest yields.

Ported from the npm package `@anthropic-ai/mcpb` (read at 1.2.0), which claude-code imports and
which the source snapshot does not contain:

* `schemas/0.3.js` (re-exported as the package's top level through `schemas/latest.js`) ->
  `MANIFEST_VERSION`, `McpServerConfigSchema`, `McpbManifestAuthorSchema`,
  `McpbManifestRepositorySchema`, `McpbManifestPlatformOverrideSchema`,
  `McpbManifestMcpConfigSchema`, `McpbManifestServerSchema`, `McpbManifestCompatibilitySchema`,
  `McpbManifestToolSchema`, `McpbManifestPromptSchema`, `McpbUserConfigurationOptionSchema`,
  `McpbUserConfigValuesSchema`, `McpbManifestLocalizationSchema`, `McpbManifestIconSchema`,
  `McpbManifestSchema`;
* `shared/config.js` -> `replaceVariables`, `getMcpConfigForManifest`,
  `hasRequiredConfigMissing`.

Why 1.x: claude-code reads `McpbManifestSchema` from the package's top level
(`src/utils/dxt/helpers.ts`), which 1.x exports (`schemas/latest.js` = the 0.3 schema) and 2.x no
longer does; 1.2.0 is the last 1.x release. A manifest must therefore declare
`manifest_version` (or the deprecated `dxt_version`) `"0.3"`.

Why a port: the package is JavaScript (Zod 3 schemas). The schema is checked here by a small
validator that reports Zod 3's issues (`path`, `message`) in Zod 3's order and wording
(`Required`, `Expected string, received number`, `Invalid url`, `Invalid email`, `Invalid
literal value, expected "0.3"`, `Invalid enum value. Expected 'a' | 'b', received 'c'`,
`Unrecognized key(s) in object: 'x'`, `Invalid input` for a union), because
`src/utils/dxt/helpers.ts` turns them into its error message. Zod's `.url()` (`new URL`) is
approximated by `app/utils/plugins/schemas.is_url`, as in the plugin schemas.
`getMcpConfigForManifest`'s `logger` option and `replaceVariables`' `console.warn` are this
module's logger. JavaScript's `String.replace` replacement patterns (`$$`, `$&`, `` $` ``, `$'`)
are honoured when a variable's value is substituted.
"""

from __future__ import annotations

import logging
import math
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from app.utils.plugins.schemas import is_url

logger = logging.getLogger("TDDOrchestrator.Plugins")

#: `MANIFEST_VERSION`.
MANIFEST_VERSION = "0.3"

_LOCALE_PLACEHOLDER = re.compile(r"\$\{locale\}", re.IGNORECASE)
_BCP47 = re.compile(r"^[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{1,8})*\Z", re.ASCII)
_ICON_SIZE = re.compile(r"^\d+x\d+\Z", re.ASCII)
#: Zod 3's `emailRegex`.
_EMAIL = re.compile(
    r"^(?!\.)(?!.*\.\.)([A-Z0-9_'+\-\.]*)[A-Z0-9_+-]@([A-Z0-9][A-Z0-9\-]*\.)+[A-Z]{2,}\Z",
    re.IGNORECASE | re.ASCII,
)
_USER_CONFIG_ITEM = re.compile(r"^\$\{user_config\.[^}]+\}\Z")
_VARIABLE_NAME = re.compile(r"^\$\{([^}]+)\}\Z")

#: `McpbManifest`: the parsed manifest, a plain dict as Zod returns.
McpbManifest: TypeAlias = dict[str, Any]
#: `McpbUserConfigurationOption`.
McpbUserConfigurationOption: TypeAlias = dict[str, Any]
#: `McpbUserConfigValues`.
McpbUserConfigValues: TypeAlias = dict[str, Any]


# ── a Zod 3 validator ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class McpbIssue:
    """A Zod 3 issue: `path` and `message`."""

    path: tuple[str | int, ...]
    message: str
    #: Zod 3 aborts the enclosing objects on a type, literal, enum or union failure (`INVALID`);
    #: a string check or an unknown key only marks them dirty, and a `.refine` still runs.
    fatal: bool = field(default=True, compare=False, repr=False)


@dataclass(frozen=True)
class McpbParseResult:
    """`safeParse`'s result: `success` with `data`, or the `issues`."""

    success: bool
    data: McpbManifest | None
    issues: tuple[McpbIssue, ...] = ()


_Path: TypeAlias = tuple[str | int, ...]
#: A validator: the value and its path in, the issues it found appended.
_Check: TypeAlias = Callable[[Any, _Path, list[McpbIssue]], None]
_ABSENT = object()


def _parsed_type(value: Any) -> str:
    """Zod 3's `getParsedType`."""
    if value is _ABSENT:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "nan" if isinstance(value, float) and math.isnan(value) else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _type_issue(expected: str, value: Any, path: _Path) -> McpbIssue:
    received = _parsed_type(value)
    message = "Required" if received == "undefined" else f"Expected {expected}, received {received}"
    return McpbIssue(path, message)


def _string(*refinements: Callable[[str], str | None]) -> _Check:
    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if not isinstance(value, str):
            issues.append(_type_issue("string", value, path))
            return
        for refine in refinements:
            message = refine(value)
            if message is not None:
                issues.append(McpbIssue(path, message, fatal=False))

    return check


def _url(value: str) -> str | None:
    return None if is_url(value) else "Invalid url"


def _email(value: str) -> str | None:
    return None if _EMAIL.search(value) else "Invalid email"


def _regex(pattern: re.Pattern[str], message: str) -> Callable[[str], str | None]:
    return lambda value: None if pattern.search(value) else message


def _min_length(minimum: int, message: str) -> Callable[[str], str | None]:
    return lambda value: None if len(value) >= minimum else message


def _check_number(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
    if _parsed_type(value) != "number":
        issues.append(_type_issue("number", value, path))


def _check_boolean(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
    if not isinstance(value, bool):
        issues.append(_type_issue("boolean", value, path))


def _check_any(_value: Any, _path: _Path, _issues: list[McpbIssue]) -> None:
    return None


def _literal(expected: str) -> _Check:
    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if value != expected or not isinstance(value, str):
            issues.append(McpbIssue(path, f'Invalid literal value, expected "{expected}"'))

    return check


def _enum(options: Sequence[str]) -> _Check:
    joined = " | ".join(f"'{o}'" for o in options)

    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if not isinstance(value, str):
            issues.append(_type_issue(joined, value, path))
        elif value not in options:
            issues.append(McpbIssue(path, f"Invalid enum value. Expected {joined}, received '{value}'"))

    return check


def _array(item: _Check) -> _Check:
    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if not isinstance(value, list):
            issues.append(_type_issue("array", value, path))
            return
        for index, element in enumerate(value):
            item(element, (*path, index), issues)

    return check


def _record(item: _Check) -> _Check:
    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if not isinstance(value, dict):
            issues.append(_type_issue("object", value, path))
            return
        for key, element in value.items():
            item(element, (*path, key), issues)

    return check


def _union(*branches: _Check) -> _Check:
    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        for branch in branches:
            branch_issues: list[McpbIssue] = []
            branch(value, path, branch_issues)
            if not branch_issues:
                return
        issues.append(McpbIssue(path, "Invalid input"))

    return check


@dataclass(frozen=True)
class _Field:
    check: _Check
    optional: bool = False


def _opt(check: _Check) -> _Field:
    return _Field(check, optional=True)


def _strict_object(shape: Mapping[str, _Field | _Check], refine: Callable[[dict[str, Any]], str | None] | None = None,
                   ) -> _Check:
    """`z.strictObject(shape)` (and a whole-object `.refine`, run only when the object parsed)."""
    fields = {k: v if isinstance(v, _Field) else _Field(v) for k, v in shape.items()}

    def check(value: Any, path: _Path, issues: list[McpbIssue]) -> None:
        if not isinstance(value, dict):
            issues.append(_type_issue("object", value, path))
            return
        before = len(issues)
        for key, spec in fields.items():
            field_value = value.get(key, _ABSENT)
            if field_value is _ABSENT and spec.optional:
                continue
            spec.check(field_value, (*path, key), issues)
        unknown = [k for k in value if k not in fields]
        if unknown:
            keys = ", ".join(f"'{k}'" for k in unknown)
            issues.append(McpbIssue(path, f"Unrecognized key(s) in object: {keys}", fatal=False))
        if refine is not None and not any(i.fatal for i in issues[before:]):
            message = refine(value)
            if message is not None:
                issues.append(McpbIssue(path, message, fatal=False))

    return check


_McpServerConfigShape: dict[str, _Field | _Check] = {
    "command": _string(),
    "args": _opt(_array(_string())),
    "env": _opt(_record(_string())),
}
#: `McpServerConfigSchema`.
McpServerConfigSchema = _strict_object(_McpServerConfigShape)
#: `McpbManifestAuthorSchema`.
McpbManifestAuthorSchema = _strict_object({
    "name": _string(),
    "email": _opt(_string(_email)),
    "url": _opt(_string(_url)),
})
#: `McpbManifestRepositorySchema`.
McpbManifestRepositorySchema = _strict_object({"type": _string(), "url": _string(_url)})
#: `McpbManifestPlatformOverrideSchema`: `McpServerConfigSchema.partial()`.
McpbManifestPlatformOverrideSchema = _strict_object({
    key: _opt(spec.check if isinstance(spec, _Field) else spec) for key, spec in _McpServerConfigShape.items()
})
#: `McpbManifestMcpConfigSchema`: `McpServerConfigSchema.extend({platform_overrides})`.
McpbManifestMcpConfigSchema = _strict_object({
    **_McpServerConfigShape,
    "platform_overrides": _opt(_record(McpbManifestPlatformOverrideSchema)),
})
#: `McpbManifestServerSchema`.
McpbManifestServerSchema = _strict_object({
    "type": _enum(("python", "node", "binary")),
    "entry_point": _string(),
    "mcp_config": McpbManifestMcpConfigSchema,
})
#: `McpbManifestCompatibilitySchema`.
McpbManifestCompatibilitySchema = _strict_object({
    "claude_desktop": _opt(_string()),
    "platforms": _opt(_array(_enum(("darwin", "win32", "linux")))),
    "runtimes": _opt(_strict_object({"python": _opt(_string()), "node": _opt(_string())})),
})
#: `McpbManifestToolSchema`.
McpbManifestToolSchema = _strict_object({"name": _string(), "description": _opt(_string())})
#: `McpbManifestPromptSchema`.
McpbManifestPromptSchema = _strict_object({
    "name": _string(),
    "description": _opt(_string()),
    "arguments": _opt(_array(_string())),
    "text": _string(),
})
_ConfigValue = _union(_string(), _check_number, _check_boolean, _array(_string()))
#: `McpbUserConfigurationOptionSchema`.
McpbUserConfigurationOptionSchema = _strict_object({
    "type": _enum(("string", "number", "boolean", "directory", "file")),
    "title": _string(),
    "description": _string(),
    "required": _opt(_check_boolean),
    "default": _opt(_ConfigValue),
    "multiple": _opt(_check_boolean),
    "sensitive": _opt(_check_boolean),
    "min": _opt(_check_number),
    "max": _opt(_check_number),
})
#: `McpbUserConfigValuesSchema`.
McpbUserConfigValuesSchema = _record(_ConfigValue)
#: `McpbManifestLocalizationSchema`.
McpbManifestLocalizationSchema = _strict_object({
    "resources": _string(_regex(_LOCALE_PLACEHOLDER, 'resources must include a "${locale}" placeholder')),
    "default_locale": _string(_regex(_BCP47, "default_locale must be a valid BCP 47 locale identifier")),
})
#: `McpbManifestIconSchema`.
McpbManifestIconSchema = _strict_object({
    "src": _string(),
    "size": _string(_regex(_ICON_SIZE, 'size must be in the format "WIDTHxHEIGHT" (e.g., "16x16")')),
    "theme": _opt(_string(_min_length(1, "theme cannot be empty when provided"))),
})


def _has_version(data: dict[str, Any]) -> str | None:
    if data.get("dxt_version") or data.get("manifest_version"):
        return None
    return "Either 'dxt_version' (deprecated) or 'manifest_version' must be provided"


#: `McpbManifestSchema`.
McpbManifestSchema = _strict_object(
    {
        "$schema": _opt(_string()),
        "dxt_version": _opt(_literal(MANIFEST_VERSION)),
        "manifest_version": _opt(_literal(MANIFEST_VERSION)),
        "name": _string(),
        "display_name": _opt(_string()),
        "version": _string(),
        "description": _string(),
        "long_description": _opt(_string()),
        "author": McpbManifestAuthorSchema,
        "repository": _opt(McpbManifestRepositorySchema),
        "homepage": _opt(_string(_url)),
        "documentation": _opt(_string(_url)),
        "support": _opt(_string(_url)),
        "icon": _opt(_string()),
        "icons": _opt(_array(McpbManifestIconSchema)),
        "screenshots": _opt(_array(_string())),
        "localization": _opt(McpbManifestLocalizationSchema),
        "server": McpbManifestServerSchema,
        "tools": _opt(_array(McpbManifestToolSchema)),
        "tools_generated": _opt(_check_boolean),
        "prompts": _opt(_array(McpbManifestPromptSchema)),
        "prompts_generated": _opt(_check_boolean),
        "keywords": _opt(_array(_string())),
        "license": _opt(_string()),
        "privacy_policies": _opt(_array(_string(_url))),
        "compatibility": _opt(McpbManifestCompatibilitySchema),
        "user_config": _opt(_record(McpbUserConfigurationOptionSchema)),
        "_meta": _opt(_record(_record(_check_any))),
    },
    refine=_has_version,
)


def safe_parse_manifest(value: Any) -> McpbParseResult:
    """`McpbManifestSchema.safeParse(value)`."""
    issues: list[McpbIssue] = []
    McpbManifestSchema(value, (), issues)
    if issues:
        return McpbParseResult(False, None, tuple(issues))
    return McpbParseResult(True, value)


# ── shared/config.js ──────────────────────────────────────────────────────────


def _js_replacement(template: str, match: re.Match[str]) -> str:
    """JavaScript's `String.replace` replacement patterns for a match without groups."""
    out: list[str] = []
    i = 0
    while i < len(template):
        char = template[i]
        nxt = template[i + 1] if i + 1 < len(template) else ""
        if char == "$" and nxt == "$":
            out.append("$")
        elif char == "$" and nxt == "&":
            out.append(match.group(0))
        elif char == "$" and nxt == "`":
            out.append(match.string[:match.start()])
        elif char == "$" and nxt == "'":
            out.append(match.string[match.end():])
        else:
            out.append(char)
            i += 1
            continue
        i += 2
    return "".join(out)


def replace_variables(value: Any, variables: Mapping[str, str | list[str]]) -> Any:
    """
    `replaceVariables`: `${key}` in strings (an array value cannot replace inside a string and
    is warned about); an array item that is exactly `${user_config.X}` expands to the variable's
    values; objects and arrays are walked.
    """
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            pattern = re.compile(r"\$\{" + key + r"\}")
            if pattern.search(result):
                if isinstance(replacement, list):
                    logger.warning(
                        'Cannot replace %s with array value in string context: "%s"', key, value
                    )
                else:
                    template = replacement
                    result = pattern.sub(lambda m: _js_replacement(template, m), result)
        return result
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            if isinstance(item, str) and _USER_CONFIG_ITEM.search(item):
                name_match = _VARIABLE_NAME.search(item)
                var_name = name_match.group(1) if name_match else None
                found = variables.get(var_name) if var_name else None
                if found:
                    if isinstance(found, list):
                        items.extend(found)
                    else:
                        items.append(found)
                else:
                    items.append(item)
            else:
                items.append(replace_variables(item, variables))
        return items
    if isinstance(value, dict):
        return {k: replace_variables(v, variables) for k, v in value.items()}
    return value


def _is_invalid_single_value(value: Any) -> bool:
    return value is None or value == "" or value is _ABSENT


def has_required_config_missing(manifest: Mapping[str, Any], user_config: Mapping[str, Any] | None) -> bool:
    """`hasRequiredConfigMissing`: a required option absent, empty, or an empty/holey array."""
    options = manifest.get("user_config")
    if not options:
        return False
    config = user_config or {}
    for key, option in options.items():
        if option.get("required"):
            value = config.get(key, _ABSENT)
            if _is_invalid_single_value(value) or (
                isinstance(value, list) and (not value or any(_is_invalid_single_value(v) for v in value))
            ):
                return True
    return False


def node_platform(sys_platform: str | None = None) -> str:
    """Node's `process.platform` for this interpreter: `darwin`, `win32`, `linux`, else as Python names it."""
    name = sys_platform if sys_platform is not None else sys.platform
    if name.startswith("linux"):
        return "linux"
    if name in ("win32", "cygwin"):
        return "win32"
    return name


def _js_string(value: Any) -> str:
    """JavaScript's `String(value)` for a config value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join(_js_string(v) for v in value)
    return str(value)


async def get_mcp_config_for_manifest(
    manifest: Mapping[str, Any],
    extension_path: str,
    system_dirs: Mapping[str, str],
    user_config: Mapping[str, Any] | None,
    path_separator: str,
    platform: str | None = None,
) -> dict[str, Any] | None:
    """
    `getMcpConfigForManifest`: the manifest's `server.mcp_config` with this platform's
    override, `${__dirname}`, `${pathSeparator}`, `${/}`, the system directories and
    `${user_config.X}` (defaults, then the user's values) substituted. `None` when the manifest
    has no `mcp_config` or a required option is missing. `platform` is Node's
    `process.platform` (`node_platform()` when omitted).
    """
    server = manifest.get("server") or {}
    base_config = server.get("mcp_config")
    if not base_config:
        return None
    result: dict[str, Any] = dict(base_config)
    overrides = base_config.get("platform_overrides")
    current = platform if platform is not None else node_platform()
    if overrides and current in overrides:
        platform_config = overrides[current]
        result["command"] = platform_config.get("command") or result.get("command")
        result["args"] = platform_config.get("args") or result.get("args")
        result["env"] = platform_config.get("env") or result.get("env")
    if has_required_config_missing(manifest, user_config):
        logger.warning("Extension %s has missing required configuration, skipping MCP config", manifest.get("name"))
        return None
    variables: dict[str, str | list[str]] = {
        "__dirname": extension_path,
        "pathSeparator": path_separator,
        "/": path_separator,
        **system_dirs,
    }
    merged: dict[str, Any] = {}
    for key, option in (manifest.get("user_config") or {}).items():
        if "default" in option:
            merged[key] = option["default"]
    if user_config:
        merged.update(user_config)
    for key, value in merged.items():
        name = f"user_config.{key}"
        if isinstance(value, list):
            variables[name] = [_js_string(v) for v in value]
        else:
            variables[name] = _js_string(value)
    replaced: dict[str, Any] = replace_variables(result, variables)
    return replaced


__all__ = [
    "MANIFEST_VERSION",
    "McpbIssue",
    "McpbManifest",
    "McpbManifestSchema",
    "McpbParseResult",
    "McpbUserConfigValues",
    "McpbUserConfigurationOption",
    "get_mcp_config_for_manifest",
    "has_required_config_missing",
    "node_platform",
    "replace_variables",
    "safe_parse_manifest",
]
