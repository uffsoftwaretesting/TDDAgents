"""
Settings validation errors: their shape, their messages, and the permission rules filtered out first.

Ported from `reference/claude-code/src/utils/settings/validation.ts` -> `ValidationError`,
`SettingsWithErrors` (`load_settings_with_errors` returns its pair), `getReceivedType`,
`extractReceivedFromMessage`, `formatZodError`, `validateSettingsFileContent`,
`filterInvalidPermissionRules`.

`SchemaIssue` stands in for a Zod issue (`app/utils/settings/schema_validator.py` produces them from
the ported settings schema): its `code` is Zod's (`invalid_type`, `invalid_value`,
`unrecognized_keys`, `too_small`, `too_big`, `invalid_format`, `invalid_union`, `custom`), its
`message` what Zod would say, and `format_zod_error` rewrites the messages as upstream does
("Expected boolean, but received string", "Invalid value. Expected one of: ...", "Invalid or
malformed JSON" for a document that is not JSON).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.settings.validation_tips import TipContext, get_validation_tip
from app.utils.string_utils import plural


@dataclass(frozen=True, slots=True)
class ValidationError:
    """`ValidationError`: the file, the field's dotted path, the message, and how to fix it."""

    path: str
    message: str
    file: str | None = None
    expected: str | None = None
    invalid_value: Any = None
    suggestion: str | None = None
    doc_link: str | None = None
    #: `mcpErrorMetadata` (`scope`, `serverName`, `severity`): only an MCP configuration's errors carry it.
    mcp_error_metadata: Mapping[str, Any] | None = None


#: An issue's input was absent (JS `undefined`), as opposed to a JSON `null`.
UNDEFINED: Any = type("Undefined", (), {"__repr__": lambda self: "undefined"})()


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    """A Zod issue: its code, the path to the value, Zod's message and the code's details."""

    code: str
    path: tuple[str | int, ...]
    message: str
    expected: str | None = None
    values: tuple[Any, ...] = ()
    keys: tuple[str, ...] = ()
    minimum: int | float | None = None
    input: Any = UNDEFINED
    #: A `custom` issue's `params.received`.
    params_received: Any = None
    has_params: bool = False


def get_received_type(value: Any) -> str:
    """`getReceivedType`: the JS `typeof` name of a JSON value."""
    if value is None:
        return "null"
    if value is UNDEFINED:
        return "undefined"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def extract_received_from_message(message: str) -> str | None:
    """`extractReceivedFromMessage`."""
    match = re.search(r"received (\w+)", message)
    return match.group(1) if match else None


def format_zod_error(issues: Sequence[SchemaIssue], file_path: str) -> list[ValidationError]:
    """`formatZodError`: each issue as a `ValidationError`, with upstream's message and tip."""
    errors: list[ValidationError] = []
    for issue in issues:
        path = ".".join(str(part) for part in issue.path)
        message = issue.message
        expected: str | None = None
        enum_values: list[str] | None = None
        expected_value: str | None = None
        received_value: Any = None
        invalid_value: Any = None
        if issue.code == "invalid_value":
            enum_values = [_js_string(v) for v in issue.values]
            expected_value = " | ".join(enum_values)
        elif issue.code == "invalid_type":
            expected_value = issue.expected
            received_value = extract_received_from_message(issue.message) or get_received_type(issue.input)
            invalid_value = received_value
        elif issue.code == "too_small":
            expected_value = _js_string(issue.minimum)
        elif issue.code == "custom" and issue.has_params:
            received_value = issue.params_received
            invalid_value = received_value
        tip = get_validation_tip(TipContext(path=path, code=issue.code, expected=expected_value,
                                            received=received_value, enum_values=enum_values,
                                            message=issue.message, value=received_value))
        if issue.code == "invalid_value":
            expected = ", ".join(f'"{v}"' for v in enum_values or [])
            message = f"Invalid value. Expected one of: {expected}"
        elif issue.code == "invalid_type":
            received_type = extract_received_from_message(issue.message) or get_received_type(issue.input)
            if issue.expected == "object" and received_type == "null" and path == "":
                message = "Invalid or malformed JSON"
            else:
                message = f"Expected {issue.expected}, but received {received_type}"
        elif issue.code == "unrecognized_keys":
            message = f"Unrecognized {plural(len(issue.keys), 'field')}: {', '.join(issue.keys)}"
        elif issue.code == "too_small":
            message = f"Number must be greater than or equal to {_js_string(issue.minimum)}"
            expected = _js_string(issue.minimum)
        errors.append(ValidationError(
            file=file_path, path=path, message=message, expected=expected, invalid_value=invalid_value,
            suggestion=tip.suggestion if tip is not None else None, doc_link=tip.doc_link if tip is not None else None))
    return errors


def _js_string(value: Any) -> str:
    """`String(value)` for a JSON value."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True, slots=True)
class SettingsFileContentValidation:
    """`validateSettingsFileContent`'s result: valid, or the error and the full schema to show with it."""

    is_valid: bool
    error: str = ""
    full_schema: str = ""


def validate_settings_file_content(content: str) -> SettingsFileContentValidation:
    """
    `validateSettingsFileContent`: the text parsed as JSON and checked against `SettingsSchema().strict()`
    (no permission rule filtered out first), for an edit to a settings file.
    """
    from app.utils.settings.schema_output import generate_settings_json_schema
    from app.utils.settings.schema_validator import safe_parse_settings

    try:
        data = json.loads(content)
    except ValueError as exc:
        # `jsonParse` throws; the message is the parser's (Python's wording, not V8's).
        return SettingsFileContentValidation(False, f"Invalid JSON: {exc}", generate_settings_json_schema())
    parsed, issues = safe_parse_settings(data, strict=True)
    if parsed is not None:
        return SettingsFileContentValidation(True)
    errors = format_zod_error(issues, "settings")
    message = "Settings validation failed:\n" + "\n".join(f"- {e.path}: {e.message}" for e in errors)
    return SettingsFileContentValidation(False, message, generate_settings_json_schema())


def filter_invalid_permission_rules(data: Any, file_path: str) -> list[ValidationError]:
    """
    `filterInvalidPermissionRules`: drop the invalid rules from `permissions.allow` / `deny` / `ask` (in
    place, before the schema sees them, so one bad rule does not reject the file), with a warning each.
    """
    from app.utils.settings.permission_validation import validate_permission_rule

    if not isinstance(data, MutableMapping):
        return []
    permissions = data.get("permissions")
    if not isinstance(permissions, MutableMapping) or not permissions:
        return []
    warnings: list[ValidationError] = []
    for key in ("allow", "deny", "ask"):
        rules = permissions.get(key)
        if not isinstance(rules, list):
            continue
        kept: list[Any] = []
        for rule in rules:
            if not isinstance(rule, str):
                warnings.append(ValidationError(file=file_path, path=f"permissions.{key}",
                                                message=f"Non-string value in {key} array was removed",
                                                invalid_value=rule))
                continue
            result = validate_permission_rule(rule)
            if not result.valid:
                message = f'Invalid permission rule "{rule}" was skipped'
                if result.error:
                    message += f": {result.error}"
                if result.suggestion:
                    message += f". {result.suggestion}"
                warnings.append(ValidationError(file=file_path, path=f"permissions.{key}", message=message,
                                                invalid_value=rule))
                continue
            kept.append(rule)
        permissions[key] = kept
    return warnings


__all__ = [
    "SchemaIssue",
    "SettingsFileContentValidation",
    "UNDEFINED",
    "ValidationError",
    "extract_received_from_message",
    "filter_invalid_permission_rules",
    "format_zod_error",
    "get_received_type",
    "validate_settings_file_content",
]
