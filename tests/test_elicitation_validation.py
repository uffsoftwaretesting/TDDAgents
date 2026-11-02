"""
An MCP elicitation form's checks (`app/utils/mcp/elicitation_validation.py`) and the natural-language
date parse (`app/utils/mcp/date_time_parser.py`). The expected messages and verdicts were read from
zod 3.25's `v4` build, which upstream validates with.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.utils.mcp.date_time_parser import (
    PARSE_FAILED,
    SYSTEM_PROMPT,
    UNABLE_TO_PARSE,
    build_user_prompt,
    looks_like_iso_8601,
    parse_natural_language_date_time,
)
from app.utils.mcp.elicitation_validation import (
    ValidationResult,
    format_js_number,
    get_enum_label,
    get_enum_labels,
    get_enum_values,
    get_format_hint,
    get_multi_select_label,
    get_multi_select_labels,
    get_multi_select_values,
    is_date_time_schema,
    is_enum_schema,
    is_multi_select_enum_schema,
    js_number,
    validate_elicitation_input,
    validate_elicitation_input_async,
)

LEGACY = {"type": "string", "enum": ["a", "b"], "enumNames": ["Alpha", "Beta"]}
ONE_OF = {"type": "string", "oneOf": [{"const": "x", "title": "Ex"}, {"const": "y", "title": "Why"}]}
MULTI_ANY = {"type": "array", "items": {"anyOf": [{"const": "r", "title": "Red"}, {"const": "g", "title": "Green"}]}}
MULTI_ENUM = {"type": "array", "items": {"enum": ["p", "q"]}}


def test_enum_schemas_and_labels() -> None:
    assert is_enum_schema(LEGACY) and is_enum_schema(ONE_OF)
    assert not is_enum_schema({"type": "string"}) and not is_enum_schema({"type": "number", "enum": [1]})
    assert get_enum_values(LEGACY) == ["a", "b"] and get_enum_labels(LEGACY) == ["Alpha", "Beta"]
    assert get_enum_values(ONE_OF) == ["x", "y"] and get_enum_labels(ONE_OF) == ["Ex", "Why"]
    assert get_enum_labels({"type": "string", "enum": ["a"]}) == ["a"]
    assert get_enum_values({"type": "string"}) == []
    assert get_enum_label(ONE_OF, "y") == "Why" and get_enum_label(ONE_OF, "z") == "z"
    assert get_enum_label({"type": "string", "enum": ["a", "b"], "enumNames": ["A"]}, "b") == "b"


def test_multi_select_schemas_and_labels() -> None:
    assert is_multi_select_enum_schema(MULTI_ANY) and is_multi_select_enum_schema(MULTI_ENUM)
    assert not is_multi_select_enum_schema({"type": "array", "items": {"type": "string"}})
    assert not is_multi_select_enum_schema({"type": "array"})
    assert not is_multi_select_enum_schema({"type": "string", "items": {"enum": ["a"]}})
    assert get_multi_select_values(MULTI_ANY) == ["r", "g"] and get_multi_select_labels(MULTI_ANY) == ["Red", "Green"]
    assert get_multi_select_values(MULTI_ENUM) == ["p", "q"] == get_multi_select_labels(MULTI_ENUM)
    assert get_multi_select_values({"type": "array", "items": "x"}) == []
    assert get_multi_select_label(MULTI_ANY, "g") == "Green" and get_multi_select_label(MULTI_ANY, "z") == "z"
    short = {"type": "array", "items": {"anyOf": [{"const": "r", "title": "Red"}]}}
    assert get_multi_select_label(short, "r") == "Red"


@pytest.mark.parametrize("text, expected", [
    ("3", 3.0), (" 4 ", 4.0), ("", 0.0), ("  ", 0.0), ("+5", 5.0), (".5", 0.5), ("5.", 5.0), ("1e1", 10.0),
    ("0x10", 16.0), ("0o7", 7.0), ("0b11", 3.0), ("Infinity", math.inf), ("-Infinity", -math.inf),
    ("+Infinity", math.inf),
])
def test_js_number(text: str, expected: float) -> None:
    assert js_number(text) == expected


@pytest.mark.parametrize("text", ["abc", "1_000", "NaN", "0xZZ", "1 2", "-0x10"])
def test_js_number_nan(text: str) -> None:
    assert math.isnan(js_number(text))


def test_format_js_number() -> None:
    assert format_js_number(3.0) == "3" and format_js_number(2.5) == "2.5" and format_js_number(7) == "7"


INT_2_5 = {"type": "integer", "minimum": 2, "maximum": 5}


@pytest.mark.parametrize("schema, text, result", [
    (INT_2_5, "3", ValidationResult(True, 3)),
    (INT_2_5, " 4 ", ValidationResult(True, 4)),
    (INT_2_5, "1.5", ValidationResult(False, error="Must be an integer between 2 and 5")),
    (INT_2_5, "7", ValidationResult(False, error="Must be an integer between 2 and 5")),
    (INT_2_5, "abc", ValidationResult(False, error="Must be an integer between 2 and 5")),
    (INT_2_5, "Infinity", ValidationResult(False, error="Must be an integer between 2 and 5")),
    ({"type": "number"}, "3.5", ValidationResult(True, 3.5)),
    ({"type": "number"}, "", ValidationResult(True, 0)),
    ({"type": "number"}, "x", ValidationResult(False, error="Must be a number")),
    ({"type": "number", "minimum": 1}, "0", ValidationResult(False, error="Must be a number >= 1.0")),
    ({"type": "number", "maximum": 1.5}, "2", ValidationResult(False, error="Must be a number <= 1.5")),
    ({"type": "number", "minimum": 1, "maximum": 2}, "1", ValidationResult(True, 1)),
    ({"type": "number", "minimum": 1, "maximum": 2}, "2", ValidationResult(True, 2)),
    ({"type": "integer", "minimum": 1}, "0", ValidationResult(False, error="Must be an integer >= 1")),
    ({"type": "integer", "maximum": 3}, "4", ValidationResult(False, error="Must be an integer <= 3")),
    ({"type": "integer"}, "2.5", ValidationResult(False, error="Must be an integer")),
    ({"type": "boolean"}, "false", ValidationResult(True, True)),
    ({"type": "boolean"}, "", ValidationResult(True, False)),
    (LEGACY, "a", ValidationResult(True, "a")),
    (LEGACY, "c", ValidationResult(False, error='Invalid option: expected one of "a"|"b"')),
    ({"type": "string", "enum": []}, "c",
     ValidationResult(False, error="Invalid input: expected never, received string")),
    ({"type": "string"}, " any ", ValidationResult(True, " any ")),
    ({"type": "string", "minLength": 5, "format": "email"}, "ab",
     ValidationResult(False, error="Must be at least 5 characters; "
                                   "Must be a valid email address, e.g. user@example.com")),
    ({"type": "string", "minLength": 1}, "", ValidationResult(False, error="Must be at least 1 character")),
    ({"type": "string", "maxLength": 2}, "abc", ValidationResult(False, error="Must be at most 2 characters")),
    ({"type": "string", "maxLength": 1}, "ab", ValidationResult(False, error="Must be at most 1 character")),
    ({"type": "string", "maxLength": 3}, "abc", ValidationResult(True, "abc")),
    ({"type": "string", "minLength": 3}, "abc", ValidationResult(True, "abc")),
])
def test_validate_elicitation_input(schema: dict[str, Any], text: str, result: ValidationResult) -> None:
    assert validate_elicitation_input(text, schema) == result


@pytest.mark.parametrize("text, ok", [
    ("user@example.com", True), ("a.b+c@d.co", True), ("a_b@x-y.org", True), ("o'n@x.io", True),
    ("a@b", False), ("a..b@c.com", False), (".a@b.com", False), ("a@-b.com", False), ("a@b.c", False),
])
def test_email(text: str, ok: bool) -> None:
    assert validate_elicitation_input(text, {"type": "string", "format": "email"}).is_valid is ok


@pytest.mark.parametrize("text, ok", [
    ("https://example.com", True), ("mailto:x@y", True), ("foo:bar", True), ("file:///tmp", True),
    ("http://user@host:80/p?q#f", True), ("example.com", False), ("http://", False), ("a b://c", False),
    ("http://exa mple.com", False), ("https://:80", False),
])
def test_uri(text: str, ok: bool) -> None:
    result = validate_elicitation_input(text, {"type": "string", "format": "uri"})
    assert result.is_valid is ok
    assert result.error == (None if ok else "Must be a valid URI, e.g. https://example.com")


@pytest.mark.parametrize("text, ok", [
    ("2024-03-15", True), ("2024-02-29", True), ("2000-02-29", True), ("2024-02-30", False), ("2023-02-29", False),
    ("1900-02-29", False), ("2024-13-01", False), ("2024-3-5", False), ("2024-04-31", False),
])
def test_date(text: str, ok: bool) -> None:
    result = validate_elicitation_input(text, {"type": "string", "format": "date"})
    assert result.is_valid is ok
    assert result.error == (None if ok else "Must be a valid date, e.g. 2024-03-15, today, next Monday")


@pytest.mark.parametrize("text, ok", [
    ("2024-03-15T14:30:00Z", True), ("2024-03-15T14:30Z", True), ("2024-03-15T14:30:00+02:00", True),
    ("2024-03-15T14:30:00.123Z", True), ("2024-03-15T14:30:00+0200", False), ("2024-03-15T14:30:00", False),
    ("2024-03-15T25:30:00Z", False), ("2024-03-15T14:30:00+02", False), ("2024-02-30T14:30:00Z", False),
    ("2024-03-15t14:30:00z", False),
])
def test_date_time(text: str, ok: bool) -> None:
    result = validate_elicitation_input(text, {"type": "string", "format": "date-time"})
    assert result.is_valid is ok
    assert result.error == (None if ok else "Must be a valid date-time, e.g. 2024-03-15T14:30:00Z, tomorrow at 3pm")


def test_unsupported_schema_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported schema"):
        validate_elicitation_input("x", {"type": "object"})


@pytest.mark.parametrize("schema, hint", [
    ({"type": "string", "format": "email"}, "email address, e.g. user@example.com"),
    ({"type": "string", "format": "date-time"}, "date-time, e.g. 2024-03-15T14:30:00Z"),
    ({"type": "string", "format": "weird"}, "undefined, e.g. undefined"),
    ({"type": "string"}, None),
    ({"type": "number", "minimum": 1, "maximum": 2}, "(number between 1.0 and 2.0)"),
    ({"type": "integer", "minimum": 1}, "(integer >= 1)"),
    ({"type": "number", "maximum": 2.5}, "(number <= 2.5)"),
    ({"type": "integer"}, "(integer, e.g. 42)"),
    ({"type": "number"}, "(number, e.g. 3.14)"),
    ({"type": "boolean"}, None),
])
def test_get_format_hint(schema: dict[str, Any], hint: str | None) -> None:
    assert get_format_hint(schema) == hint


def test_is_date_time_schema() -> None:
    assert is_date_time_schema({"type": "string", "format": "date"})
    assert is_date_time_schema({"type": "string", "format": "date-time"})
    assert not is_date_time_schema({"type": "string", "format": "email"})
    assert not is_date_time_schema({"type": "number", "format": "date"})


class Haiku:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


DATE = {"type": "string", "format": "date"}


def test_async_validation_parses_natural_language_dates() -> None:
    haiku = Haiku(" 2026-11-03 \n")
    result = asyncio.run(validate_elicitation_input_async("tomorrow", DATE, haiku))
    assert result == ValidationResult(True, "2026-11-03") and len(haiku.calls) == 1


@pytest.mark.parametrize("text, schema", [
    ("2026-11-03", DATE),                       # valid already
    ("2026-02-30", DATE),                       # looks like ISO: no parse
    ("tomorrow", {"type": "string"}),           # valid plain string
    ("abc", {"type": "integer"}),               # not a date field
])
def test_async_validation_asks_the_model_only_for_a_failing_natural_language_date(text: str, schema: Any) -> None:
    haiku = Haiku("2026-11-03")
    asyncio.run(validate_elicitation_input_async(text, schema, haiku))
    assert haiku.calls == []


@pytest.mark.parametrize("answer", ["INVALID", "", "next week", "2026-13-45", RuntimeError("down")])
def test_async_validation_keeps_the_sync_error_when_the_parse_fails(answer: Any) -> None:
    result = asyncio.run(validate_elicitation_input_async("someday", DATE, Haiku(answer)))
    assert result == validate_elicitation_input("someday", DATE)


def test_async_validation_without_a_model() -> None:
    assert not asyncio.run(validate_elicitation_input_async("tomorrow", DATE)).is_valid


NOW = datetime(2026, 11, 2, 10, 30, 15, 250000, tzinfo=timezone(timedelta(hours=-3)))


def test_user_prompt() -> None:
    assert build_user_prompt("tomorrow", "date", NOW) == (
        "Current context:\n- Current date and time: 2026-11-02T13:30:15.250Z (UTC)\n- Local timezone: -03:00\n"
        "- Day of week: Monday\n\nUser input: \"tomorrow\"\n\nOutput format: YYYY-MM-DD (date only, no time)\n\n"
        "Parse the user's input into ISO 8601 format. Return ONLY the formatted string, or \"INVALID\" if the input "
        "is incomplete or unparseable.")
    plus = NOW.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert "Output format: YYYY-MM-DDTHH:MM:SS+05:30 (full date-time with timezone)" in build_user_prompt(
        "x", "date-time", plus)
    assert "Local timezone: +00:00" in build_user_prompt("x", "date", NOW.replace(tzinfo=None))


def test_parse_natural_language_date_time() -> None:
    haiku = Haiku("2026-11-03T15:00:00-03:00")
    result = asyncio.run(parse_natural_language_date_time("tomorrow at 3pm", "date-time", haiku, NOW))
    assert result.success and result.value == "2026-11-03T15:00:00-03:00"
    assert haiku.calls[0][0] == SYSTEM_PROMPT and SYSTEM_PROMPT.startswith("You are a date/time parser")
    assert 'User input: "tomorrow at 3pm"' in haiku.calls[0][1]
    for answer in ("INVALID", "  ", "soon"):
        failed = asyncio.run(parse_natural_language_date_time("x", "date", Haiku(answer), NOW))
        assert not failed.success and failed.error == UNABLE_TO_PARSE
    crashed = asyncio.run(parse_natural_language_date_time("x", "date", Haiku(RuntimeError("down")), NOW))
    assert not crashed.success and crashed.error == PARSE_FAILED
    assert asyncio.run(parse_natural_language_date_time("x", "date", Haiku("2026-01-01"))).success


@pytest.mark.parametrize("text, iso", [
    ("2025-01-01", True), (" 2025-01-01T10:00 ", True), ("2025-01-01 10:00", False), ("2025-1-1", False),
    ("tomorrow", False),
])
def test_looks_like_iso_8601(text: str, iso: bool) -> None:
    assert looks_like_iso_8601(text) is iso
