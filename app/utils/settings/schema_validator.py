"""
`SettingsSchema().safeParse` for this build: the ported settings schema, checked the way Zod checks it.

Upstream validates every settings document against its Zod `SettingsSchema`
(`reference/claude-code/src/utils/settings/types.ts`) and ignores a document that fails
(`settings.ts` -> `parseSettingsFileUncached`). This build has no Zod: the schema is the JSON Schema
`app/utils/settings/types.py` -> `settings_json_schema` ports (the keys this build reads), and
`safe_parse_settings` validates a document against it with `jsonschema`, reporting each failure as
the Zod issue it stands for (`app/utils/settings/validation.py` -> `SchemaIssue`), then runs the
refinements JSON Schema cannot express, as Zod runs them after the shape:

* the `hooks` block's checks (`app/schemas/hooks.py` -> `hooks_schema_errors`: an http hook's URL, ...);
* `PermissionRuleSchema`'s `.superRefine` (`app/utils/settings/permission_validation.py`): a rule in
  `permissions.allow` / `deny` / `ask` that `validate_permission_rule` rejects. A settings file has its
  invalid rules filtered out before (`filterInvalidPermissionRules`), so only an unfiltered document
  (the SDK's inline flag settings, an edit checked by `validate_settings_file_content`) meets it;
* `AllowedMcpServerEntrySchema` / `DeniedMcpServerEntrySchema`'s "exactly one of" `.refine`;
* the plugin keys, validated by their own port (`app/utils/settings/types.py` -> `parse_plugin_settings`,
  with `extraKnownMarketplaces`' `.check`), and left out of the JSON Schema run so each issue is
  reported once.

`strictPluginOnlyCustomization` is `z.preprocess(...).catch(undefined)` upstream: unknown surface names
are dropped from an array, and any other invalid value drops the key instead of failing the document.

**Unknown keys follow Zod:** the top-level object and `permissions` are `.passthrough()`, so their
unknown keys are kept; every other object strips its unknown keys from the parsed document
(`strip_unknown_keys`), as Zod returns `result.data`. Upstream's keys this build does not read are not
in the schema, so they pass through unchecked (*divergence:* a wrongly typed `model` or `env`, which
upstream rejects, is accepted here, where nothing reads it). `strict` (`SettingsSchema().strict()`) knows
their names too (`app/utils/settings/types.py` -> `STRICT_SETTINGS_KEYS`), so only a key upstream does not
declare is unrecognized.

A discriminated union (a hook's `type`, a marketplace source's `source`) reports the issues of the
branch its discriminator selects, and `invalid_union` ("Invalid input") when none matches; a plain
union reports `invalid_union`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError

from app.utils.settings.validation import UNDEFINED, SchemaIssue, get_received_type

#: The issue `AllowedMcpServerEntrySchema` / `DeniedMcpServerEntrySchema`'s `.refine` adds.
MCP_ENTRY_EXACTLY_ONE = 'Entry must have exactly one of "serverName", "serverCommand", or "serverUrl"'
#: `.regex(..., message)`: the custom messages of the patterns this schema checks.
PATTERN_MESSAGES: dict[str, str] = {
    "^[a-zA-Z0-9_-]+$": "Server name can only contain letters, numbers, hyphens, and underscores",
}
_MCP_ENTRY_KEYS = ("allowedMcpServers", "deniedMcpServers")
#: The `permissions` arrays of `PermissionRuleSchema` strings (its `.superRefine`).
_PERMISSION_RULE_KEYS = ("allow", "deny", "ask")
_MCP_ENTRY_FIELDS = ("serverName", "serverCommand", "serverUrl")
_STRICT_PLUGIN_ONLY = "strictPluginOnlyCustomization"


@cache
def _schema() -> dict[str, Any]:
    from app.utils.settings.types import PLUGIN_SETTINGS_KEYS, settings_json_schema

    schema = settings_json_schema()
    properties = {k: v for k, v in schema["properties"].items() if k not in PLUGIN_SETTINGS_KEYS}
    return {**schema, "properties": properties}


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(_schema())


def _js_type(schema_type: Any) -> str:
    """A JSON Schema type as Zod names it (`integer` is a `number`)."""
    if isinstance(schema_type, list):
        return " | ".join(_js_type(t) for t in schema_type)
    return "number" if schema_type == "integer" else str(schema_type)


def _invalid_type(path: tuple[str | int, ...], expected: str, value: Any) -> SchemaIssue:
    return SchemaIssue(code="invalid_type", path=path, expected=expected, input=value,
                       message=f"Invalid input: expected {expected}, received {get_received_type(value)}")


def _missing(path: tuple[str | int, ...], schema: Mapping[str, Any]) -> SchemaIssue:
    """A required property that is absent: what Zod reports for its schema given `undefined`."""
    if "const" in schema:
        return SchemaIssue(code="invalid_value", path=path, values=(schema["const"],), input=UNDEFINED,
                           message="Invalid input")
    if "enum" in schema:
        return SchemaIssue(code="invalid_value", path=path, values=tuple(schema["enum"]), input=UNDEFINED,
                           message="Invalid option")
    if "type" in schema:
        return _invalid_type(path, _js_type(schema["type"]), UNDEFINED)
    return SchemaIssue(code="invalid_union", path=path, input=UNDEFINED, message="Invalid input")


def _discriminator(branches: Sequence[Any]) -> str | None:
    """The property every branch fixes with a `const` (a `z.discriminatedUnion`'s key), if any."""
    if not branches or not all(isinstance(b, Mapping) and isinstance(b.get("properties"), Mapping) for b in branches):
        return None
    common = set(branches[0]["properties"])
    for branch in branches[1:]:
        common &= set(branch["properties"])
    for key in sorted(common):
        if all(isinstance(b["properties"][key], Mapping) and "const" in b["properties"][key] for b in branches):
            return str(key)
    return None


def _issues(error: JsonSchemaError) -> list[SchemaIssue]:
    """The Zod issues one `jsonschema` error stands for."""
    path = tuple(error.absolute_path)
    validator = error.validator
    value = error.instance
    rule: Any = error.validator_value
    if "propertyNames" in error.absolute_schema_path:
        # `z.record(z.enum(...), ...)` with a key outside the enum.
        return [SchemaIssue(code="invalid_key", path=(*path, str(value)), input=value,
                            message="Invalid key in record")]
    if validator == "type":
        return [_invalid_type(path, _js_type(rule), value)]
    if validator == "enum":
        return [SchemaIssue(code="invalid_value", path=path, values=tuple(rule), input=value, message="Invalid option")]
    if validator == "const":
        return [SchemaIssue(code="invalid_value", path=path, values=(rule,), input=value, message="Invalid input")]
    if validator == "required":
        name = ast.literal_eval(error.message.split(" is a required property")[0])
        schema = (error.schema.get("properties") or {}).get(name, {}) if isinstance(error.schema, Mapping) else {}
        return [_missing((*path, name), schema)]
    if validator in ("minItems", "minLength", "exclusiveMinimum"):
        minimum = 0 if validator == "exclusiveMinimum" else rule
        return [SchemaIssue(code="too_small", path=path, minimum=minimum, input=value, message="Too small")]
    if validator == "maxLength":
        return [SchemaIssue(code="too_big", path=path, input=value,
                            message=f"Too big: expected string to have <={rule} characters")]
    if validator == "pattern":
        pattern = str(rule)
        message = PATTERN_MESSAGES.get(pattern, f"Invalid string: must match pattern /{pattern}/")
        return [SchemaIssue(code="invalid_format", path=path, input=value, message=message)]
    if validator == "anyOf":
        branches = list(rule)
        key = _discriminator(branches)
        if key is not None:
            if not isinstance(value, Mapping):
                # `$ZodDiscriminatedUnion` checks the input is an object before reading its discriminator.
                return [_invalid_type(path, "object", value)]
            matching = [i for i, b in enumerate(branches) if b["properties"][key]["const"] == value.get(key)]
            if matching:
                inner = [e for e in error.context or () if e.relative_schema_path[0] == matching[0]]
                return [issue for e in inner for issue in _issues(e)]
            return [SchemaIssue(code="invalid_union", path=(*path, key), input=value.get(key, UNDEFINED),
                                message="Invalid input")]
        return [SchemaIssue(code="invalid_union", path=path, input=value, message="Invalid input")]
    return [SchemaIssue(code="custom", path=path, input=value, message=error.message)]


def _split(line: str) -> SchemaIssue:
    """A `path: message` issue line (the hooks and plugin validators') as a `custom` issue."""
    where, separator, message = line.partition(": ")
    if not separator:
        return SchemaIssue(code="custom", path=(), message=line)
    return SchemaIssue(code="custom", path=tuple(int(p) if p.isdigit() else p for p in where.split(".") if p),
                       message=message)


def _preprocess(document: Mapping[str, Any]) -> dict[str, Any]:
    """`strictPluginOnlyCustomization`'s `z.preprocess(...).catch(undefined)`."""
    from app.utils.settings.types import CUSTOMIZATION_SURFACES

    data = dict(document)
    if _STRICT_PLUGIN_ONLY in data:
        value = data[_STRICT_PLUGIN_ONLY]
        if isinstance(value, list):
            data[_STRICT_PLUGIN_ONLY] = [v for v in value if isinstance(v, str) and v in CUSTOMIZATION_SURFACES]
        elif not isinstance(value, bool):
            del data[_STRICT_PLUGIN_ONLY]
    return data


def _refinements(data: Mapping[str, Any], shape_issues: Iterable[SchemaIssue]) -> list[SchemaIssue]:
    """The checks JSON Schema cannot express, on the values whose shape passed."""
    from app.schemas.hooks import hooks_schema_errors
    from app.utils.settings.permission_validation import permission_rule_issue_message, validate_permission_rule

    paths = [issue.path for issue in shape_issues]
    failed = {path[0] for path in paths if path}
    failed_entries = {path[:2] for path in paths if len(path) >= 2}
    issues: list[SchemaIssue] = []
    permissions = data.get("permissions")
    if isinstance(permissions, Mapping):
        for key in _PERMISSION_RULE_KEYS:
            rules = permissions.get(key)
            if not isinstance(rules, list):
                continue
            for index, rule in enumerate(rules):
                result = validate_permission_rule(rule) if isinstance(rule, str) else None
                if result is not None and not result.valid:
                    issues.append(SchemaIssue(code="custom", path=("permissions", key, index), input=rule,
                                              message=permission_rule_issue_message(result), has_params=True,
                                              params_received=rule))
    if "hooks" in data and "hooks" not in failed:
        issues.extend(_split(line) for line in hooks_schema_errors(data["hooks"]))
    for key in _MCP_ENTRY_KEYS:
        entries = data.get(key)
        if (key,) in paths or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if (key, index) in failed_entries:
                continue
            if isinstance(entry, Mapping) and sum(f in entry for f in _MCP_ENTRY_FIELDS) != 1:
                issues.append(SchemaIssue(code="custom", path=(key, index), input=entry,
                                          message=MCP_ENTRY_EXACTLY_ONE))
    return issues


#: A union branch's validator, by the branch (the schema is built once, so its parts live as long).
_BRANCH_VALIDATORS: dict[int, tuple[Mapping[str, Any], Draft202012Validator]] = {}


def _branch_validator(branch: Mapping[str, Any]) -> Draft202012Validator:
    cached = _BRANCH_VALIDATORS.get(id(branch))
    if cached is None or cached[0] is not branch:
        cached = (branch, Draft202012Validator(branch))
        _BRANCH_VALIDATORS[id(branch)] = cached
    return cached[1]


def strip_unknown_keys(schema: Mapping[str, Any], value: Any) -> Any:
    """
    The parsed value Zod returns for a valid input: an object without `additionalProperties` loses its
    unknown keys; a `.passthrough()` object keeps them; a record and an array are stripped item by item;
    a union strips with the first branch the value satisfies.
    """
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            if _branch_validator(branch).is_valid(value):
                return strip_unknown_keys(branch, value)
        return value
    if isinstance(value, dict) and schema.get("type") == "object":
        properties: Mapping[str, Any] = schema.get("properties") or {}
        extra = schema.get("additionalProperties")
        stripped: dict[str, Any] = {}
        for key, item in value.items():
            if key in properties:
                stripped[key] = strip_unknown_keys(properties[key], item)
            elif isinstance(extra, Mapping):
                stripped[key] = strip_unknown_keys(extra, item) if extra else item
        return stripped
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        return [strip_unknown_keys(schema["items"], item) for item in value]
    return value


def safe_parse_settings(document: Any, *, strict: bool = False) -> tuple[dict[str, Any] | None, list[SchemaIssue]]:
    """
    `SettingsSchema().safeParse(document)`: the parsed document, or `None` and its issues. `strict` is
    `SettingsSchema().strict()`: a top-level key outside the schema is an `unrecognized_keys` issue.
    """
    from app.utils.settings.types import STRICT_SETTINGS_KEYS, parse_plugin_settings

    if not isinstance(document, Mapping):
        return None, [_invalid_type((), "object", document)]
    data = _preprocess(document)
    issues = [issue for error in _validator().iter_errors(data) for issue in _issues(error)]
    issues.extend(_refinements(data, issues))
    unrecognized = tuple(str(key) for key in document if key not in STRICT_SETTINGS_KEYS) if strict else ()
    if unrecognized:
        noun = "key" if len(unrecognized) == 1 else "keys"
        issues.append(SchemaIssue(code="unrecognized_keys", path=(), keys=unrecognized, input=document,
                                  message=f"Unrecognized {noun}: " + ", ".join(f'"{k}"' for k in unrecognized)))
    plugin_errors, plugin_keys = parse_plugin_settings(data)
    issues.extend(_split(line) for line in plugin_errors)
    if issues:
        return None, issues
    return {**strip_unknown_keys(_schema(), data), **plugin_keys}, []


__all__ = ["MCP_ENTRY_EXACTLY_ONE", "PATTERN_MESSAGES", "safe_parse_settings", "strip_unknown_keys"]
