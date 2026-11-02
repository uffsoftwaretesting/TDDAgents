"""
Validate plugin and marketplace manifests and plugin contents (the `plugin validate` linter).

Ported from `reference/claude-code/src/utils/plugins/validatePlugin.ts` -> `MARKETPLACE_ONLY_MANIFEST_FIELDS`,
`ValidationResult`, `ValidationError`, `ValidationWarning`, `detectManifestType`, `formatZodErrors`,
`checkPathTraversal`, `marketplaceSourceHint`, `validatePluginManifest`, `validateMarketplaceManifest`,
`validateComponentFile`, `validateHooksJson`, `collectMarkdown`, `validatePluginContents`,
`validateManifest`.

The runtime schemas strip unknown keys; this developer tool reports them, as upstream's
`.strict()` rebuilds do: the manifest's top level, and the marketplace's top level and each
plugin entry (`unrecognized_key_issues`). The
product name in the marketplace-only-field warning is "TDDAgents". Frontmatter uses the
repository's `FRONTMATTER_REGEX` and PyYAML (`yaml.safe_load`) for upstream's `parseYaml`, as the
rest of the repository's frontmatter does. PyYAML reads YAML 1.1 where upstream's parser reads
1.2's core schema, so an unquoted `yes`/`no`/`on`/`off` is a boolean and an unquoted date a date:
the type checks then report `name: yes` or `description: 2024-01-01` where upstream accepts them
as strings (recorded divergence).

Unknown keys are reported together with the other issues, as a Zod `.strict()` object does, after
them (a nested entry's before the top level's).
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from app.utils.claudemd import FRONTMATTER_REGEX
from app.utils.plugins.plugin_directories import PLUGIN_PACKAGE_DIR
from app.utils.plugins.schemas import (
    PluginHooksSchema,
    PluginManifest,
    PluginManifestSchema,
    PluginMarketplace,
    PluginMarketplaceEntry,
    PluginMarketplaceSchema,
    SchemaIssue,
    safe_parse,
    typed_dict_keys,
    unrecognized_key_issues,
)

#: `MARKETPLACE_ONLY_MANIFEST_FIELDS`: marketplace-entry fields authors copy into plugin.json.
MARKETPLACE_ONLY_MANIFEST_FIELDS: frozenset[str] = frozenset({"category", "source", "tags", "strict", "id"})

FileType = Literal["plugin", "marketplace", "skill", "agent", "command", "hooks"]

_KEBAB_CASE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z", re.ASCII)
_LEADING_PARENT_SEGMENTS = re.compile(r"^(\.\./)+")


@dataclass(frozen=True)
class ValidationError:
    """`ValidationError`."""

    path: str
    message: str
    code: str | None = None


@dataclass(frozen=True)
class ValidationWarning:
    """`ValidationWarning`."""

    path: str
    message: str


@dataclass
class ValidationResult:
    """`ValidationResult`."""

    success: bool
    file_path: str
    file_type: FileType
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)


def detect_manifest_type(file_path: str) -> Literal["plugin", "marketplace", "unknown"]:
    """`detectManifestType`: by file name, then by a plugin package folder (`PLUGIN_PACKAGE_DIR`) parent."""
    file_name = os.path.basename(file_path)
    dir_name = os.path.basename(os.path.dirname(file_path))
    if file_name == "plugin.json":
        return "plugin"
    if file_name == "marketplace.json":
        return "marketplace"
    if dir_name == PLUGIN_PACKAGE_DIR:
        return "plugin"
    return "unknown"


def format_zod_errors(issues: tuple[SchemaIssue, ...] | list[SchemaIssue]) -> list[ValidationError]:
    """`formatZodErrors`: path joined with dots (`root` when empty), message, code."""
    return [ValidationError(path=i.path_string() or "root", message=i.message, code=i.code) for i in issues]


def check_path_traversal(p: str, field_name: str, errors: list[ValidationError], hint: str | None = None) -> None:
    """`checkPathTraversal`: report a `..` in a path."""
    if ".." in p:
        message = (
            f'Path contains "..": {p}. {hint}' if hint
            else f'Path contains ".." which could be a path traversal attempt: {p}'
        )
        errors.append(ValidationError(path=field_name, message=message))


def marketplace_source_hint(p: str) -> str:
    """`marketplaceSourceHint`: the corrected `./...` path for a marketplace plugin source."""
    stripped = _LEADING_PARENT_SEGMENTS.sub("", p)
    corrected = f"./{stripped}" if stripped != p else "./plugins/my-plugin"
    return (
        "Plugin source paths are resolved relative to the marketplace root (the directory "
        f"containing {PLUGIN_PACKAGE_DIR}/), not relative to marketplace.json. "
        f'Use "{corrected}" instead of "{p}".'
    )


def _errno_name(exc: OSError) -> str | None:
    return errno.errorcode.get(exc.errno) if exc.errno is not None else None


def _read_error(absolute_path: str, exc: OSError, file_type: FileType) -> ValidationResult:
    code = _errno_name(exc)
    if code == "ENOENT":
        message = f"File not found: {absolute_path}"
    elif code == "EISDIR":
        message = f"Path is not a file: {absolute_path}"
    else:
        message = f"Failed to read file: {exc}"
    return ValidationResult(
        success=False,
        errors=[ValidationError(path="file", message=message, code=code)],
        file_path=absolute_path,
        file_type=file_type,
    )


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


async def validate_plugin_manifest(file_path: str) -> ValidationResult:
    """`validatePluginManifest`: plugin.json against the strict manifest schema, plus warnings."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    absolute_path = os.path.abspath(file_path)
    try:
        content = _read_text(absolute_path)
    except OSError as exc:
        return _read_error(absolute_path, exc, "plugin")
    try:
        parsed: Any = json.loads(content)
    except ValueError as exc:
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="json", message=f"Invalid JSON syntax: {exc}")],
            file_path=absolute_path,
            file_type="plugin",
        )

    if isinstance(parsed, dict):
        for key in ("commands", "agents", "skills"):
            if parsed.get(key):
                for i, item in enumerate(_as_list(parsed[key])):
                    if isinstance(item, str):
                        check_path_traversal(item, f"{key}[{i}]", errors)

    to_validate = parsed
    if isinstance(parsed, dict):
        stray = [k for k in parsed if k in MARKETPLACE_ONLY_MANIFEST_FIELDS]
        if stray:
            to_validate = {k: v for k, v in parsed.items() if k not in MARKETPLACE_ONLY_MANIFEST_FIELDS}
            for key in stray:
                warnings.append(ValidationWarning(
                    path=key,
                    message=(
                        f"Field '{key}' belongs in the marketplace entry (marketplace.json), not plugin.json. "
                        "It's harmless here but unused — TDDAgents ignores it at load time."
                    ),
                ))

    result = safe_parse(PluginManifestSchema, to_validate)
    issues = list(result.issues)
    # `.strict()`: unknown keys are reported with the other issues, after the shape's own.
    issues.extend(unrecognized_key_issues(to_validate, typed_dict_keys(PluginManifest)))
    errors.extend(format_zod_errors(issues))

    if not issues and result.data is not None:
        manifest = result.data
        name = manifest["name"]
        if _KEBAB_CASE.fullmatch(name) is None:
            warnings.append(ValidationWarning(
                path="name",
                message=(
                    f'Plugin name "{name}" is not kebab-case. TDDAgents accepts it, but the Claude.ai '
                    "marketplace sync requires kebab-case (lowercase letters, digits, and hyphens only, "
                    'e.g., "my-plugin").'
                ),
            ))
        if not manifest.get("version"):
            warnings.append(ValidationWarning(
                path="version",
                message='No version specified. Consider adding a version following semver (e.g., "1.0.0")',
            ))
        if not manifest.get("description"):
            warnings.append(ValidationWarning(
                path="description",
                message="No description provided. Adding a description helps users understand what your plugin does",
            ))
        if not manifest.get("author"):
            warnings.append(ValidationWarning(
                path="author",
                message="No author information provided. Consider adding author details for plugin attribution",
            ))

    return ValidationResult(
        success=not errors, errors=errors, warnings=warnings, file_path=absolute_path, file_type="plugin"
    )


async def validate_marketplace_manifest(file_path: str) -> ValidationResult:
    """`validateMarketplaceManifest`: marketplace.json, strict at the top and in each entry."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    absolute_path = os.path.abspath(file_path)
    try:
        content = _read_text(absolute_path)
    except OSError as exc:
        return _read_error(absolute_path, exc, "marketplace")
    try:
        parsed: Any = json.loads(content)
    except ValueError as exc:
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="json", message=f"Invalid JSON syntax: {exc}")],
            file_path=absolute_path,
            file_type="marketplace",
        )

    if isinstance(parsed, dict) and isinstance(parsed.get("plugins"), list):
        for i, plugin in enumerate(parsed["plugins"]):
            if not isinstance(plugin, dict) or "source" not in plugin:
                continue
            source = plugin["source"]
            if isinstance(source, str):
                check_path_traversal(source, f"plugins[{i}].source", errors, marketplace_source_hint(source))
            if isinstance(source, dict) and isinstance(source.get("path"), str):
                check_path_traversal(source["path"], f"plugins[{i}].source.path", errors)

    result = safe_parse(PluginMarketplaceSchema, parsed)
    issues = list(result.issues)
    # `.strict()`: each entry's unknown keys (nested, so first), then the top level's.
    entries = parsed.get("plugins") if isinstance(parsed, dict) else None
    if isinstance(entries, list):
        entry_keys = typed_dict_keys(PluginMarketplaceEntry)
        for i, entry in enumerate(entries):
            issues.extend(unrecognized_key_issues(entry, entry_keys, ("plugins", i)))
    issues.extend(unrecognized_key_issues(parsed, typed_dict_keys(PluginMarketplace)))
    errors.extend(format_zod_errors(issues))

    if not issues and result.data is not None:
        marketplace = result.data
        plugins = marketplace["plugins"]
        if not plugins:
            warnings.append(ValidationWarning(path="plugins", message="Marketplace has no plugins defined"))
        for i, plugin in enumerate(plugins):
            if sum(1 for p in plugins if p["name"] == plugin["name"]) > 1:
                errors.append(ValidationError(
                    path=f"plugins[{i}].name",
                    message=f'Duplicate plugin name "{plugin["name"]}" found in marketplace',
                ))
        manifest_dir = os.path.dirname(absolute_path)
        marketplace_root = (
            os.path.dirname(manifest_dir) if os.path.basename(manifest_dir) == PLUGIN_PACKAGE_DIR else manifest_dir
        )
        for i, entry in enumerate(plugins):
            source = entry["source"]
            entry_version = entry.get("version")
            if not entry_version or not isinstance(source, str) or not source.startswith("./"):
                continue
            plugin_json = os.path.join(marketplace_root, source, PLUGIN_PACKAGE_DIR, "plugin.json")
            try:
                declared: Any = json.loads(_read_text(plugin_json))
            except (OSError, ValueError):
                continue
            manifest_version = declared.get("version") if isinstance(declared, dict) else None
            if isinstance(manifest_version, str) and manifest_version and manifest_version != entry_version:
                warnings.append(ValidationWarning(
                    path=f"plugins[{i}].version",
                    message=(
                        f'Entry declares version "{entry_version}" but {source}/{PLUGIN_PACKAGE_DIR}/plugin.json says '
                        f'"{manifest_version}". At install time, plugin.json wins (calculatePluginVersion '
                        "precedence) — the entry version is silently ignored. "
                        f'Update this entry to "{manifest_version}" to match.'
                    ),
                ))
        if not (marketplace.get("metadata") or {}).get("description"):
            warnings.append(ValidationWarning(
                path="metadata.description",
                message=(
                    "No marketplace description provided. Adding a description helps users understand what "
                    "this marketplace offers"
                ),
            ))

    return ValidationResult(
        success=not errors, errors=errors, warnings=warnings, file_path=absolute_path, file_type="marketplace"
    )


def _js_typeof(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def validate_component_file(
    file_path: str, content: str, file_type: Literal["skill", "agent", "command"]
) -> ValidationResult:
    """`validateComponentFile`: the frontmatter the runtime loader would silently drop."""
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    match = FRONTMATTER_REGEX.match(content)
    if match is None:
        warnings.append(ValidationWarning(
            path="frontmatter",
            message=(
                "No frontmatter block found. Add YAML frontmatter between --- delimiters at the top of the file "
                "to set description and other metadata."
            ),
        ))
        return ValidationResult(success=True, warnings=warnings, file_path=file_path, file_type=file_type)

    try:
        parsed: Any = yaml.safe_load(match.group(1) or "")
    except yaml.YAMLError as exc:
        errors.append(ValidationError(
            path="frontmatter",
            message=(
                f"YAML frontmatter failed to parse: {exc}. At runtime this {file_type} loads with empty metadata "
                "(all frontmatter fields silently dropped)."
            ),
        ))
        return ValidationResult(success=False, errors=errors, file_path=file_path, file_type=file_type)

    if not isinstance(parsed, dict):
        got = "an array" if isinstance(parsed, list) else "null" if parsed is None else _js_typeof(parsed)
        errors.append(ValidationError(
            path="frontmatter",
            message=f"Frontmatter must be a YAML mapping (key: value pairs), got {got}.",
        ))
        return ValidationResult(success=False, errors=errors, file_path=file_path, file_type=file_type)

    fm: dict[str, Any] = parsed
    if "description" in fm:
        d = fm["description"]
        if d is not None and not isinstance(d, (str, int, float, bool)):
            got = "array" if isinstance(d, list) else _js_typeof(d)
            errors.append(ValidationError(
                path="description",
                message=f"description must be a string, got {got}. At runtime this value is dropped.",
            ))
    else:
        warnings.append(ValidationWarning(
            path="description",
            message=(
                "No description in frontmatter. A description helps users and Claude understand when to use "
                f"this {file_type}."
            ),
        ))

    name = fm.get("name")
    if name is not None and not isinstance(name, str):
        errors.append(ValidationError(path="name", message=f"name must be a string, got {_js_typeof(name)}."))

    tools = fm.get("allowed-tools")
    if tools is not None:
        if not isinstance(tools, (str, list)):
            errors.append(ValidationError(
                path="allowed-tools",
                message=f"allowed-tools must be a string or array of strings, got {_js_typeof(tools)}.",
            ))
        elif isinstance(tools, list) and any(not isinstance(t, str) for t in tools):
            errors.append(ValidationError(
                path="allowed-tools", message="allowed-tools array must contain only strings."
            ))

    shell = fm.get("shell")
    if shell is not None:
        if not isinstance(shell, str):
            errors.append(ValidationError(path="shell", message=f"shell must be a string, got {_js_typeof(shell)}."))
        elif shell.strip().lower() not in ("bash", "powershell"):
            errors.append(
                ValidationError(path="shell", message=f"shell must be 'bash' or 'powershell', got '{shell}'.")
            )

    return ValidationResult(
        success=not errors, errors=errors, warnings=warnings, file_path=file_path, file_type=file_type
    )


async def validate_hooks_json(file_path: str) -> ValidationResult:
    """`validateHooksJson`: hooks/hooks.json against `PluginHooksSchema` (absent is fine)."""
    try:
        content = _read_text(file_path)
    except FileNotFoundError:
        return ValidationResult(success=True, file_path=file_path, file_type="hooks")
    except OSError as exc:
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="file", message=f"Failed to read file: {exc}")],
            file_path=file_path,
            file_type="hooks",
        )
    try:
        parsed: Any = json.loads(content)
    except ValueError as exc:
        return ValidationResult(
            success=False,
            errors=[ValidationError(
                path="json",
                message=f"Invalid JSON syntax: {exc}. At runtime this breaks the entire plugin load.",
            )],
            file_path=file_path,
            file_type="hooks",
        )
    result = safe_parse(PluginHooksSchema, parsed)
    if not result.success:
        return ValidationResult(
            success=False, errors=format_zod_errors(result.issues), file_path=file_path, file_type="hooks"
        )
    return ValidationResult(success=True, file_path=file_path, file_type="hooks")


async def collect_markdown(directory: str, is_skills_dir: bool) -> list[str]:
    """`collectMarkdown`: skills -> `<dir>/<name>/SKILL.md` (speculative); otherwise every `.md`, recursively."""
    try:
        with os.scandir(directory) as iterator:
            entries = list(iterator)
    except (FileNotFoundError, NotADirectoryError):
        return []
    if is_skills_dir:
        return [os.path.join(directory, e.name, "SKILL.md") for e in entries if e.is_dir(follow_symlinks=False)]
    out: list[str] = []
    for entry in entries:
        full = os.path.join(directory, entry.name)
        if entry.is_dir(follow_symlinks=False):
            out.extend(await collect_markdown(full, False))
        elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".md"):
            out.append(full)
    return out


async def validate_plugin_contents(plugin_dir: str) -> list[ValidationResult]:
    """`validatePluginContents`: one result per skill/agent/command/hooks file with errors or warnings."""
    results: list[ValidationResult] = []
    dirs: list[tuple[Literal["skill", "agent", "command"], str]] = [
        ("skill", os.path.join(plugin_dir, "skills")),
        ("agent", os.path.join(plugin_dir, "agents")),
        ("command", os.path.join(plugin_dir, "commands")),
    ]
    for file_type, directory in dirs:
        for path in await collect_markdown(directory, file_type == "skill"):
            try:
                content = _read_text(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                results.append(ValidationResult(
                    success=False,
                    errors=[ValidationError(path="file", message=f"Failed to read: {exc}")],
                    file_path=path,
                    file_type=file_type,
                ))
                continue
            result = validate_component_file(path, content, file_type)
            if result.errors or result.warnings:
                results.append(result)
    hooks = await validate_hooks_json(os.path.join(plugin_dir, "hooks", "hooks.json"))
    if hooks.errors or hooks.warnings:
        results.append(hooks)
    return results


async def validate_manifest(file_path: str) -> ValidationResult:
    """`validateManifest`: a directory (marketplace.json preferred, then plugin.json) or a file, by type."""
    absolute_path = os.path.abspath(file_path)
    try:
        is_directory = stat.S_ISDIR(os.stat(absolute_path).st_mode)
    except FileNotFoundError:
        # Only ENOENT is handled here; any other stat failure propagates, as upstream's.
        is_directory = False
    if is_directory:
        marketplace_result = await validate_marketplace_manifest(
            os.path.join(absolute_path, PLUGIN_PACKAGE_DIR, "marketplace.json")
        )
        if not marketplace_result.errors or marketplace_result.errors[0].code != "ENOENT":
            return marketplace_result
        plugin_result = await validate_plugin_manifest(os.path.join(absolute_path, PLUGIN_PACKAGE_DIR, "plugin.json"))
        if not plugin_result.errors or plugin_result.errors[0].code != "ENOENT":
            return plugin_result
        return ValidationResult(
            success=False,
            errors=[ValidationError(
                path="directory",
                message=(
                    f"No manifest found in directory. Expected {PLUGIN_PACKAGE_DIR}/marketplace.json or "
                    f"{PLUGIN_PACKAGE_DIR}/plugin.json"
                ),
            )],
            file_path=absolute_path,
            file_type="plugin",
        )

    manifest_type = detect_manifest_type(file_path)
    if manifest_type == "plugin":
        return await validate_plugin_manifest(file_path)
    if manifest_type == "marketplace":
        return await validate_marketplace_manifest(file_path)
    try:
        guessed: Any = json.loads(_read_text(absolute_path))
        if isinstance(guessed, dict) and isinstance(guessed.get("plugins"), list):
            return await validate_marketplace_manifest(file_path)
    except FileNotFoundError:
        return ValidationResult(
            success=False,
            errors=[ValidationError(path="file", message=f"File not found: {absolute_path}")],
            file_path=absolute_path,
            file_type="plugin",
        )
    except (OSError, ValueError):
        pass
    return await validate_plugin_manifest(file_path)


__all__ = [
    "MARKETPLACE_ONLY_MANIFEST_FIELDS",
    "ValidationError",
    "ValidationResult",
    "ValidationWarning",
    "check_path_traversal",
    "collect_markdown",
    "detect_manifest_type",
    "format_zod_errors",
    "marketplace_source_hint",
    "validate_component_file",
    "validate_hooks_json",
    "validate_manifest",
    "validate_marketplace_manifest",
    "validate_plugin_contents",
    "validate_plugin_manifest",
]
