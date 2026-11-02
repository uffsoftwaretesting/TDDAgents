"""
`StructuredOutput`: the final answer as JSON matching the `--json-schema` the caller gave.

Ported from `reference/claude-code/src/tools/SyntheticOutputTool/SyntheticOutputTool.ts` ->
`SYNTHETIC_OUTPUT_TOOL_NAME`, `SyntheticOutputTool`, `isSyntheticOutputToolEnabled`,
`createSyntheticOutputTool` (and its `renderToolUseMessage`). The input schema is the caller's
JSON Schema; the schema itself is checked first (`ajv.validateSchema`, here `jsonschema`'s
`check_schema`), and every call validates its input against it: a mismatch is the tool's error
("Output does not match required schema: <path>: <message>, ..."), so the model tries again. A
valid call answers "Structured output provided successfully" and carries the input as the
`structured_output` attachment the engine reads (`toolExecution.ts`). The tool is read-only,
concurrency-safe and always allowed (`checkPermissions` → allow).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.tool import BuiltTool, PermissionResult, ToolContext, ToolResult, build_tool
from app.utils.frontmatter_parser import parse_markdown_frontmatter

#: `SYNTHETIC_OUTPUT_TOOL_NAME`.
SYNTHETIC_OUTPUT_TOOL_NAME = "StructuredOutput"
#: `SyntheticOutputTool.maxResultSizeChars`.
MAX_RESULT_SIZE_CHARS = 100_000
#: What a valid call answers.
STRUCTURED_OUTPUT_PROVIDED = "Structured output provided successfully"

PROMPT_PATH = Path(__file__).with_name("prompt.md")
_FRONTMATTER, SYNTHETIC_OUTPUT_PROMPT = parse_markdown_frontmatter(PROMPT_PATH.read_text(encoding="utf-8"))
SYNTHETIC_OUTPUT_DESCRIPTION = str(_FRONTMATTER["description"])


def is_synthetic_output_tool_enabled(*, is_non_interactive_session: bool) -> bool:
    """`isSyntheticOutputToolEnabled`: only a non-interactive (`-p`) session offers it."""
    return is_non_interactive_session


def render_tool_use_message(input_args: Mapping[str, Any]) -> str | None:
    """`renderToolUseMessage`: up to three `key: value` pairs, else the field count."""
    keys = list(input_args)
    if not keys:
        return None
    if len(keys) <= 3:
        return ", ".join(f"{k}: {json.dumps(input_args[k], separators=(',', ':'))}" for k in keys)
    return f"{len(keys)} fields: {', '.join(keys[:3])}…"


def schema_errors(validator: Any, input_args: Mapping[str, Any]) -> str | None:
    """Ajv's `errors` joined as `createSyntheticOutputTool` reports them; `None` when the input is valid."""
    errors = sorted(validator.iter_errors(dict(input_args)), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    return ", ".join(
        f"{'/' + '/'.join(str(p) for p in e.absolute_path) if e.absolute_path else 'root'}: {e.message}"
        for e in errors)


def create_synthetic_output_tool(json_schema: Mapping[str, Any]) -> BuiltTool | str:
    """
    `createSyntheticOutputTool`: the tool for `json_schema`, or the schema's own error (a string),
    when it is not a valid JSON Schema.
    """
    import jsonschema
    from jsonschema.exceptions import SchemaError

    schema = dict(json_schema)
    try:
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
    except SchemaError as error:
        return str(error.message)
    validator = validator_class(schema)

    def call(input_args: dict[str, Any], context: ToolContext) -> ToolResult:
        errors = schema_errors(validator, input_args)
        if errors is not None:
            return ToolResult(content=f"Output does not match required schema: {errors}", is_error=True)
        from app.utils.attachments import Attachment, create_signal_attachment_message

        signal = create_signal_attachment_message(Attachment("structured_output", {"data": dict(input_args)}))
        return ToolResult(content=STRUCTURED_OUTPUT_PROVIDED, new_messages=(signal,))

    return build_tool(
        name=SYNTHETIC_OUTPUT_TOOL_NAME,
        prompt=SYNTHETIC_OUTPUT_PROMPT,
        call=call,
        input_schema=schema,
        description=lambda args: SYNTHETIC_OUTPUT_DESCRIPTION,
        is_concurrency_safe=lambda args: True,
        is_read_only=lambda args: True,
        check_permissions=lambda args, ctx: PermissionResult(behavior="allow", updated_input=args),
        max_result_size_chars=MAX_RESULT_SIZE_CHARS,
        render_tool_use_message=lambda args, verbose: render_tool_use_message(args),
    )


__all__ = [
    "MAX_RESULT_SIZE_CHARS",
    "STRUCTURED_OUTPUT_PROVIDED",
    "SYNTHETIC_OUTPUT_DESCRIPTION",
    "SYNTHETIC_OUTPUT_PROMPT",
    "SYNTHETIC_OUTPUT_TOOL_NAME",
    "create_synthetic_output_tool",
    "is_synthetic_output_tool_enabled",
    "render_tool_use_message",
    "schema_errors",
]
