"""
Schema validation of tool input: `app/loop/tools/validation.py`.

The contract under test is what the *model* reads. Upstream's wording is reproduced
because the message is the only feedback the model gets about a call it got wrong.
"""

from __future__ import annotations

import pytest

from app.loop.tools.validation import (
    INPUT_VALIDATION_ERROR,
    format_validation_path,
)
from app.loop.tools.validation import validate_against_schema as _validate


def validate_against_schema(tool_name, schema, args) -> str:
    """Assert-and-narrow wrapper: these tests all expect a message."""
    message = _validate(tool_name, schema, args)
    assert message is not None, "expected a validation failure"
    return message


SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "limit": {"type": "integer"},
        "mode": {"type": "string", "enum": ["read", "write"]},
    },
    "required": ["file_path"],
    "additionalProperties": False,
}

TODO_SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"content": {"type": "string"}, "activeForm": {"type": "string"}},
                "required": ["content", "activeForm"],
            },
        }
    },
    "required": ["todos"],
}


@pytest.mark.parametrize("path,expected", [
    ([], ""),
    (["todos"], "todos"),
    (["todos", 0, "activeForm"], "todos[0].activeForm"),
    ([0], "[0]"),
    (["a", "b"], "a.b"),
    ([0, "a"], "[0].a"),
])
def test_paths_render_the_way_upstream_renders_them(path, expected):
    assert format_validation_path(path) == expected


def test_valid_input_produces_no_message():
    assert _validate("ReadFile", SCHEMA, {"file_path": "a.py"}) is None
    assert _validate("ReadFile", SCHEMA, {"file_path": "a.py", "limit": 5}) is None


def test_a_missing_required_parameter_is_named():
    message = validate_against_schema("ReadFile", SCHEMA, {})
    assert message == "ReadFile failed due to the following issue:\nThe required parameter `file_path` is missing"


def test_a_missing_nested_parameter_carries_its_path():
    message = validate_against_schema("TodoWrite", TODO_SCHEMA, {"todos": [{"content": "x"}]})
    assert "The required parameter `todos[0].activeForm` is missing" in message


def test_a_wrong_type_names_both_sides():
    message = validate_against_schema("ReadFile", SCHEMA, {"file_path": 7})
    assert "The parameter `file_path` type is expected as `string` but provided as `integer`" in message


@pytest.mark.parametrize("value,name", [
    (None, "null"), (True, "boolean"), (1.5, "number"), ([], "array"), ({}, "object"),
])
def test_every_json_type_is_named_in_json_terms(value, name):
    message = validate_against_schema("ReadFile", SCHEMA, {"file_path": value})
    assert f"provided as `{name}`" in message


def test_an_unexpected_parameter_is_reported():
    message = validate_against_schema("ReadFile", SCHEMA, {"file_path": "a.py", "nope": 1})
    assert "An unexpected parameter was provided" in message and "nope" in message


def test_several_failures_are_listed_together_under_a_plural_header():
    message = validate_against_schema("ReadFile", SCHEMA, {"limit": "five", "mode": "delete"})
    assert message.startswith("ReadFile failed due to the following issues:\n")
    assert "`file_path` is missing" in message
    assert "`limit` type is expected as `integer`" in message
    assert "`mode` is invalid" in message
    assert len(message.splitlines()) == 4


def test_a_single_failure_uses_the_singular_header():
    message = validate_against_schema("ReadFile", SCHEMA, {})
    assert "the following issue:" in message


def test_an_enum_violation_falls_back_to_the_validator_message():
    message = validate_against_schema("ReadFile", SCHEMA, {"file_path": "a", "mode": "delete"})
    assert "The parameter `mode` is invalid:" in message


def test_a_missing_or_empty_schema_checks_nothing():
    assert _validate("X", None, {"anything": 1}) is None
    assert _validate("X", {}, {"anything": 1}) is None


def test_an_unusable_schema_is_not_blamed_on_the_model():
    """A broken schema is our bug; reporting it as a bad call would mislead the model."""
    assert _validate("X", {"type": "not-a-type"}, {"a": 1}) is None


def test_a_root_level_type_mismatch_is_still_reported():
    assert "ReadFile failed" in validate_against_schema("ReadFile", SCHEMA, "a string, not an object")


def test_the_prefix_constant_matches_upstream():
    assert INPUT_VALIDATION_ERROR == "InputValidationError"


# ── the remaining branches of the message builder ───────────────────────────

def test_a_string_where_a_number_belongs_is_named_as_a_string():
    message = validate_against_schema("ReadFile", SCHEMA, {"file_path": "a", "limit": "five"})
    assert "The parameter `limit` type is expected as `integer` but provided as `string`" in message


def test_a_union_type_lists_its_alternatives():
    schema = {"type": "object", "properties": {"x": {"type": ["string", "null"]}}}
    message = validate_against_schema("T", schema, {"x": 1})
    assert "expected as `string|null`" in message


def test_a_value_of_no_json_type_is_named_by_its_python_type():
    class Exotic:
        pass

    message = validate_against_schema("T", {"type": "object", "properties": {"x": {"type": "string"}}},
                                      {"x": Exotic()})
    assert "provided as `Exotic`" in message


def test_a_failure_with_no_path_and_no_family_falls_back_to_the_validator():
    message = validate_against_schema("T", {"type": "object", "minProperties": 2}, {"a": 1})
    assert message.startswith("T failed due to the following issue:\n")
    assert "too short" in message or "minProperties" in message or "does not have enough" in message


def test_exactly_two_failures_already_use_the_plural():
    message = validate_against_schema("ReadFile", SCHEMA, {"limit": "five", "mode": "delete"})
    lines = message.splitlines()
    assert lines[0] == "ReadFile failed due to the following issues:"
    assert len(lines) == 4


def test_two_failures_read_exactly_like_this():
    schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
              "required": ["a", "b"]}
    assert validate_against_schema("T", schema, {}) == (
        "T failed due to the following issues:\n"
        "The required parameter `a` is missing\n"
        "The required parameter `b` is missing"
    )


def test_one_failure_reads_exactly_like_this():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert validate_against_schema("T", schema, {}) == (
        "T failed due to the following issue:\nThe required parameter `a` is missing"
    )
