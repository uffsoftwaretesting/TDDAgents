"""
Schema validation of tool input, and the message the model gets when it fails (Phase 2).

Ported from:
- `reference/claude-code/src/services/tools/toolExecution.ts` -> the `tool.inputSchema.safeParse(input)`
  that opens `checkPermissionsAndCallTool`, with the comment that explains why it exists:
  *"Validate input types with zod (surprisingly, the model is not great at generating valid input)"*
- `reference/claude-code/src/utils/toolErrors.ts` -> `formatZodValidationError`, `formatValidationPath`

Upstream uses zod; the equivalent here is `jsonschema` against the `input_schema` each tool
already declares, so nothing had to be restated in a second dialect.

Two details from upstream that are easy to lose and worth keeping:

* **The model gets the humanized message, the transcript gets the raw one.** Upstream sends
  `<tool_use_error>InputValidationError: ${errorContent}</tool_use_error>` to the model and
  stores `parsedInput.error.message` in `toolUseResult`.
* **`errorCode` never reaches the model.** It is read at exactly one place upstream, inside
  an analytics call, and its values are per-tool opaque integers with no shared meaning.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as SchemaValidationError

#: `toolExecution.ts`'s prefix on the model-facing message.
INPUT_VALIDATION_ERROR = "InputValidationError"


def format_validation_path(path: Iterable[Any]) -> str:
    """
    `formatValidationPath`: `['todos', 0, 'activeForm']` becomes `todos[0].activeForm`.
    """
    rendered = ""
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered


def _missing_property(error: SchemaValidationError) -> str | None:
    """The property name from a `required` failure, which jsonschema reports on the parent."""
    if error.validator != "required":
        return None
    message = str(error.message)
    start = message.find("'")
    end = message.find("'", start + 1)
    if start == -1 or end == -1:
        return None
    name = message[start + 1:end]
    parent = format_validation_path(error.absolute_path)
    return f"{parent}.{name}" if parent else name


def describe_error(error: SchemaValidationError) -> str:
    """
    One English sentence per failure, in upstream's three families.

    Anything that matches none of them falls back to the validator's own message, exactly
    as `formatZodValidationError` falls back to `error.message`.
    """
    missing = _missing_property(error)
    if missing is not None:
        return f"The required parameter `{missing}` is missing"

    path = format_validation_path(error.absolute_path)
    if error.validator == "additionalProperties":
        return f"An unexpected parameter was provided: {error.message}"
    if error.validator == "type" and path:
        expected = error.validator_value
        expected_name = "|".join(expected) if isinstance(expected, list) else str(expected)
        return (
            f"The parameter `{path}` type is expected as `{expected_name}` "
            f"but provided as `{_type_name(error.instance)}`"
        )
    if path:
        return f"The parameter `{path}` is invalid: {error.message}"
    return str(error.message)


def _type_name(value: Any) -> str:
    """The JSON Schema name for a Python value, so both sides of the message agree."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def format_schema_validation_error(tool_name: str, errors: list[SchemaValidationError]) -> str:
    """`formatZodValidationError`, header wording included."""
    parts = [describe_error(error) for error in errors]
    noun = "issues" if len(parts) > 1 else "issue"
    return f"{tool_name} failed due to the following {noun}:\n" + "\n".join(parts)


def validate_against_schema(
    tool_name: str, schema: Mapping[str, Any] | None, args: Any
) -> str | None:
    """
    Validate `args` against `schema`. Returns the model-facing message, or `None` if valid.

    A schema that is itself unusable is **not** the model's fault and must not be reported
    as a bad call: upstream's schemas are compiled objects that cannot be malformed at this
    point, so the equivalent failure here is treated as "no schema to check against".
    """
    if not schema:
        return None
    try:
        validator = Draft7Validator(dict(schema))
        errors = sorted(validator.iter_errors(args), key=lambda e: list(e.absolute_path))
    except Exception:  # noqa: BLE001 - an invalid schema is not the model's error; validation is skipped
        return None
    if not errors:
        return None
    return format_schema_validation_error(tool_name, errors)


__all__ = [
    "INPUT_VALIDATION_ERROR",
    "describe_error",
    "format_schema_validation_error",
    "format_validation_path",
    "validate_against_schema",
]
