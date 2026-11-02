"""
Reading and checking what the user types into an MCP elicitation form.

Ported from `reference/claude-code/src/utils/mcp/elicitationValidation.ts` -> `isEnumSchema`,
`isMultiSelectEnumSchema`, `getMultiSelectValues`, `getMultiSelectLabels`, `getMultiSelectLabel`,
`getEnumValues`, `getEnumLabels`, `getEnumLabel`, `getZodSchema`, `validateElicitationInput`,
`getFormatHint`, `isDateTimeSchema`, `validateElicitationInputAsync`.

Upstream validates with zod (`zod/v4`): `getZodSchema` builds a schema per field and
`safeParse` reports every failing check, joined by `"; "`. Here the same checks are written out,
with zod's own messages and patterns where upstream relies on them (read at zod 3.25's `v4`
build): the enum message, the email, date and date-time patterns, `z.coerce.number()` (`Number(text)`:
blank is 0, hex/octal/binary literals count, infinities do not) and `z.coerce.boolean()`
(`Boolean(text)`). An integer check that fails stops there, as zod's does. A `uri` is accepted
when it parses as an absolute URL (`new URL(text)`); WHATWG parsing is approximated (a scheme,
then a host for the special schemes) and the text is kept as typed, not normalized to `href`.

A schema is the JSON object an MCP server sent (`requestedSchema.properties[<name>]`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.utils.mcp.date_time_parser import QueryHaiku, looks_like_iso_8601, parse_natural_language_date_time
from app.utils.string_utils import plural

Schema = Mapping[str, Any]
Value = str | int | float | bool


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """`ValidationResult`."""

    is_valid: bool
    value: Value | None = None
    error: str | None = None


#: `STRING_FORMATS`: each format's description and example.
STRING_FORMATS: dict[str, tuple[str, str]] = {
    "email": ("email address", "user@example.com"),
    "uri": ("URI", "https://example.com"),
    "date": ("date", "2024-03-15"),
    "date-time": ("date-time", "2024-03-15T14:30:00Z"),
}

#: zod's `regexes.email`.
EMAIL_PATTERN = re.compile(
    r"^(?!\.)(?!.*\.\.)([A-Za-z0-9_'+\-\.]*)[A-Za-z0-9_+-]@([A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$")
#: zod's `dateSource`: a calendar date, leap years included.
_DATE_SOURCE = (
    r"(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:"
    r"(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))")
DATE_PATTERN = re.compile(f"^{_DATE_SOURCE}$")
#: zod's `datetime({ offset: true })`: `Z` or a `±hh:mm` offset, seconds and fractions optional.
DATE_TIME_PATTERN = re.compile(
    f"^{_DATE_SOURCE}T(?:(?:[01]\\d|2[0-3]):[0-5]\\d(?::[0-5]\\d(?:\\.\\d+)?)?(?:Z|([+-]\\d{{2}}:\\d{{2}})))$")
#: A URL scheme (`new URL` requires one).
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$", re.DOTALL)
#: WHATWG's special schemes, which need a host (`file` excepted).
_SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})
#: JavaScript's `Number(text)` for a decimal literal.
_DECIMAL = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_RADIX = {"0x": 16, "0o": 8, "0b": 2}


def is_enum_schema(schema: Schema) -> bool:
    """`isEnumSchema`: a single-select enum (legacy `enum` or `oneOf`)."""
    return schema.get("type") == "string" and ("enum" in schema or "oneOf" in schema)


def is_multi_select_enum_schema(schema: Schema) -> bool:
    """`isMultiSelectEnumSchema`: `type: "array"` with `items.enum` or `items.anyOf`."""
    items = schema.get("items")
    return schema.get("type") == "array" and isinstance(items, Mapping) and ("enum" in items or "anyOf" in items)


def _items(schema: Schema) -> Mapping[str, Any]:
    items = schema.get("items")
    return items if isinstance(items, Mapping) else {}


def get_multi_select_values(schema: Schema) -> list[str]:
    """`getMultiSelectValues`."""
    items = _items(schema)
    if "anyOf" in items:
        return [str(item.get("const")) for item in items["anyOf"]]
    return [str(value) for value in items.get("enum", [])]


def get_multi_select_labels(schema: Schema) -> list[str]:
    """`getMultiSelectLabels`."""
    items = _items(schema)
    if "anyOf" in items:
        return [str(item.get("title")) for item in items["anyOf"]]
    return [str(value) for value in items.get("enum", [])]


def get_multi_select_label(schema: Schema, value: str) -> str:
    """`getMultiSelectLabel`: the value's label, else the value."""
    values = get_multi_select_values(schema)
    if value not in values:
        return value
    labels = get_multi_select_labels(schema)
    index = values.index(value)
    return labels[index] if index < len(labels) else value


def get_enum_values(schema: Schema) -> list[str]:
    """`getEnumValues`: `oneOf` consts, else `enum`."""
    if "oneOf" in schema:
        return [str(item.get("const")) for item in schema["oneOf"]]
    return [str(value) for value in schema.get("enum", [])]


def get_enum_labels(schema: Schema) -> list[str]:
    """`getEnumLabels`: `oneOf` titles, else `enumNames`, else `enum`."""
    if "oneOf" in schema:
        return [str(item.get("title")) for item in schema["oneOf"]]
    names = schema.get("enumNames")
    return [str(value) for value in (names if names is not None else schema.get("enum", []))]


def get_enum_label(schema: Schema, value: str) -> str:
    """`getEnumLabel`: the value's label, else the value."""
    values = get_enum_values(schema)
    if value not in values:
        return value
    labels = get_enum_labels(schema)
    index = values.index(value)
    return labels[index] if index < len(labels) else value


def js_number(text: str) -> float:
    """`Number(text)`: blank is 0; a non-number is NaN."""
    stripped = text.strip()
    if not stripped:
        return 0.0
    if _DECIMAL.match(stripped):
        return float(stripped)
    radix = _RADIX.get(stripped[:2].lower())
    if radix is not None:
        try:
            return float(int(stripped[2:], radix))
        except ValueError:
            return math.nan
    if stripped.lstrip("+-") == "Infinity":
        return -math.inf if stripped.startswith("-") else math.inf
    return math.nan


def format_js_number(n: float) -> str:
    """`String(n)` for a finite number: integral values without a fraction."""
    return str(int(n)) if float(n).is_integer() else repr(float(n))


def _format_num(n: float, is_integer: bool) -> str:
    """`formatNum`: an integral bound reads `2.0` for a `number` field."""
    return f"{format_js_number(n)}.0" if float(n).is_integer() and not is_integer else format_js_number(n)


def _number_value(n: float) -> int | float:
    """The parsed number as JSON would carry it: integral values as integers."""
    return int(n) if n.is_integer() else n


def _range_message(schema: Schema) -> str:
    is_integer = schema.get("type") == "integer"
    type_label = "an integer" if is_integer else "a number"
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None and maximum is not None:
        return (f"Must be {type_label} between {_format_num(minimum, is_integer)} and "
                f"{_format_num(maximum, is_integer)}")
    if minimum is not None:
        return f"Must be {type_label} >= {_format_num(minimum, is_integer)}"
    if maximum is not None:
        return f"Must be {type_label} <= {_format_num(maximum, is_integer)}"
    return f"Must be {type_label}"


def _is_url(text: str) -> bool:
    """`new URL(text)` succeeds (approximated)."""
    match = _SCHEME.match(text.strip())
    if match is None or any(c.isspace() for c in text.strip()):
        return False
    scheme, rest = match.group(1).lower(), match.group(2)
    if scheme in _SPECIAL_SCHEMES:
        host = rest.lstrip("/\\").split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        return bool(host.rsplit("@", 1)[-1].split(":", 1)[0])
    return True


def _string_issues(text: str, schema: Schema) -> list[str]:
    issues: list[str] = []
    min_length, max_length = schema.get("minLength"), schema.get("maxLength")
    if min_length is not None and len(text) < min_length:
        issues.append(f"Must be at least {min_length} {plural(min_length, 'character')}")
    if max_length is not None and len(text) > max_length:
        issues.append(f"Must be at most {max_length} {plural(max_length, 'character')}")
    fmt = schema.get("format")
    if fmt == "email" and not EMAIL_PATTERN.match(text):
        issues.append("Must be a valid email address, e.g. user@example.com")
    elif fmt == "uri" and not _is_url(text):
        issues.append("Must be a valid URI, e.g. https://example.com")
    elif fmt == "date" and not DATE_PATTERN.match(text):
        issues.append("Must be a valid date, e.g. 2024-03-15, today, next Monday")
    elif fmt == "date-time" and not DATE_TIME_PATTERN.match(text):
        issues.append("Must be a valid date-time, e.g. 2024-03-15T14:30:00Z, tomorrow at 3pm")
    return issues


def _number_issue(n: float, schema: Schema) -> str | None:
    message = _range_message(schema)
    if not math.isfinite(n):
        return message
    if schema.get("type") == "integer" and not n.is_integer():
        return message
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if (minimum is not None and n < minimum) or (maximum is not None and n > maximum):
        return message
    return None


def validate_elicitation_input(string_value: str, schema: Schema) -> ValidationResult:
    """`validateElicitationInput`: the typed text as the field's value, or every failed check."""
    kind = schema.get("type")
    if is_enum_schema(schema):
        values = get_enum_values(schema)
        if string_value in values:
            return ValidationResult(True, string_value)
        if not values:
            return ValidationResult(False, error="Invalid input: expected never, received string")
        expected = "|".join(f'"{value}"' for value in values)
        return ValidationResult(False, error=f"Invalid option: expected one of {expected}")
    if kind == "string":
        issues = _string_issues(string_value, schema)
        return ValidationResult(False, error="; ".join(issues)) if issues else ValidationResult(True, string_value)
    if kind in ("number", "integer"):
        n = js_number(string_value)
        issue = _number_issue(n, schema)
        return ValidationResult(False, error=issue) if issue else ValidationResult(True, _number_value(n))
    if kind == "boolean":
        return ValidationResult(True, bool(string_value))
    raise ValueError(f"Unsupported schema: {dict(schema)}")


def get_format_hint(schema: Schema) -> str | None:
    """`getFormatHint`: a string format's description and example, or a number's range."""
    kind = schema.get("type")
    if kind == "string":
        fmt = schema.get("format")
        if not isinstance(fmt, str):
            return None
        description, example = STRING_FORMATS.get(fmt, ("undefined", "undefined"))
        return f"{description}, e.g. {example}"
    if kind in ("number", "integer"):
        is_integer = kind == "integer"
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if minimum is not None and maximum is not None:
            return f"({kind} between {_format_num(minimum, is_integer)} and {_format_num(maximum, is_integer)})"
        if minimum is not None:
            return f"({kind} >= {_format_num(minimum, is_integer)})"
        if maximum is not None:
            return f"({kind} <= {_format_num(maximum, is_integer)})"
        return f"({kind}, e.g. {'42' if is_integer else '3.14'})"
    return None


def is_date_time_schema(schema: Schema) -> bool:
    """`isDateTimeSchema`: a `date` or `date-time` string, which natural language may fill."""
    return schema.get("type") == "string" and schema.get("format") in ("date", "date-time")


async def validate_elicitation_input_async(
    string_value: str, schema: Schema, query_haiku: QueryHaiku | None = None
) -> ValidationResult:
    """
    `validateElicitationInputAsync`: the synchronous check; for a date or date-time that fails
    it and does not look like ISO 8601, the natural-language parse (`query_haiku`, the small fast
    model), checked again. Without `query_haiku` there is no natural-language parse.
    """
    result = validate_elicitation_input(string_value, schema)
    if result.is_valid:
        return result
    if query_haiku is not None and is_date_time_schema(schema) and not looks_like_iso_8601(string_value):
        parsed = await parse_natural_language_date_time(string_value, str(schema.get("format")), query_haiku)
        if parsed.success:
            checked = validate_elicitation_input(parsed.value, schema)
            if checked.is_valid:
                return checked
    return result


__all__ = [
    "DATE_PATTERN",
    "DATE_TIME_PATTERN",
    "EMAIL_PATTERN",
    "STRING_FORMATS",
    "Schema",
    "ValidationResult",
    "Value",
    "format_js_number",
    "get_enum_label",
    "get_enum_labels",
    "get_enum_values",
    "get_format_hint",
    "get_multi_select_label",
    "get_multi_select_labels",
    "get_multi_select_values",
    "is_date_time_schema",
    "is_enum_schema",
    "is_multi_select_enum_schema",
    "js_number",
    "validate_elicitation_input",
    "validate_elicitation_input_async",
]
