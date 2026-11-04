"""
Building JSON Schema documents the way upstream's Zod schemas emit them.

Upstream declares its settings schema with Zod and prints it with Zod 4's `toJSONSchema`
(`reference/claude-code/src/utils/settings/schemaOutput.ts`, `skills/bundled/updateConfig.ts` ->
`generateSettingsSchema`). This build has no Zod, so the schemas that `update-config` prints are
declared with these helpers, one per Zod construct, each producing the fragment `toJSONSchema`
produces for it (input mode):

    z.string()                    {"type": "string"}  (+ minLength, pattern, format "uri")
    z.number().positive()         {"type": "number", "exclusiveMinimum": 0}  (+ minimum, maximum)
    z.number().int()              {"type": "integer", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER}
    z.boolean()                   {"type": "boolean"}
    z.literal(x) / z.enum([...])  {"type": "string", "const": x} / {"type": "string", "enum": [...]}
    z.array(T)                    {"type": "array", "items": T}  (+ minItems)
    z.record(K, V)                {"type": "object", "propertyNames": K, "additionalProperties": V}
    z.object({...})               {"type": "object", "properties": {...}, "required": [...]}
    .passthrough()                + "additionalProperties": {}
    z.union / discriminatedUnion  {"anyOf": [...]}
    .describe(text)               "description": text, first

`.int()` sets the safe-integer bounds over any bound declared before it: `z.number().nonnegative().int()`
prints `.int()`'s bounds alone, and the minimum is enforced by the validator
(`app/utils/settings/types.py` -> `NONNEGATIVE_INTEGER_SETTINGS_KEYS`). A bound declared after `.int()`
(`.int().positive()`, `.int().min(-1)`) replaces `.int()`'s.

Refinements Zod cannot express in JSON Schema (`.refine`, `.superRefine`, `.check`) are absent
from its output too; the validators of this build enforce them (`app/schemas/hooks.py`,
`app/utils/settings/types.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

JsonSchema = dict[str, Any]
#: An object property: its schema and whether it is required (`.optional()` makes it not).
Property = tuple[JsonSchema, bool]

#: `toJSONSchema`'s default target, written as the document's `$schema`.
JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
#: `Number.MAX_SAFE_INTEGER`: the bounds `.int()` (Zod's `safeint` format) checks and prints.
MAX_SAFE_INTEGER = 9007199254740991


def describe(schema: JsonSchema, text: str) -> JsonSchema:
    """`.describe(text)`: Zod writes the description before the schema it describes."""
    return {"description": text, **{key: value for key, value in schema.items() if key != "description"}}


def string(*, min_length: int | None = None, length: int | None = None, pattern: str | None = None,
           url: bool = False) -> JsonSchema:
    """`z.string()` with `.min`, `.length`, `.regex` / `.startsWith`, `.url()`."""
    schema: JsonSchema = {"type": "string"}
    if min_length is not None:
        schema["minLength"] = min_length
    if length is not None:
        schema["minLength"] = length
        schema["maxLength"] = length
    if url:
        schema["format"] = "uri"
    if pattern is not None:
        schema["pattern"] = pattern
    return schema


def number(*, positive: bool = False, minimum: float | None = None, maximum: float | None = None) -> JsonSchema:
    """`z.number()`, `.positive()`, `.min(n)` / `.nonnegative()`, `.max(n)`."""
    schema: JsonSchema = {"type": "number"}
    if positive:
        schema["exclusiveMinimum"] = 0
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def integer(*, positive: bool = False, minimum: int | None = None) -> JsonSchema:
    """`z.number().int()`, then `.positive()` or `.min(n)`: a bound after `.int()` replaces its lower bound."""
    schema: JsonSchema = {"type": "integer"}
    if positive:
        schema["exclusiveMinimum"] = 0
    else:
        schema["minimum"] = -MAX_SAFE_INTEGER if minimum is None else minimum
    schema["maximum"] = MAX_SAFE_INTEGER
    return schema


def boolean() -> JsonSchema:
    """`z.boolean()`."""
    return {"type": "boolean"}


def literal(value: str) -> JsonSchema:
    """`z.literal(value)`."""
    return {"type": "string", "const": value}


def enum(values: Sequence[str]) -> JsonSchema:
    """`z.enum(values)`."""
    return {"type": "string", "enum": list(values)}


def array(items: JsonSchema, *, min_items: int | None = None) -> JsonSchema:
    """`z.array(items)`, `.min(n)` (Zod writes `minItems` first)."""
    schema: JsonSchema = {} if min_items is None else {"minItems": min_items}
    return {**schema, "type": "array", "items": items}


def record(values: JsonSchema, keys: JsonSchema | None = None) -> JsonSchema:
    """`z.record(keys, values)` (and `z.partialRecord`: an enum of keys, none required)."""
    return {"type": "object", "propertyNames": keys if keys is not None else string(),
            "additionalProperties": values}


def union(*options: JsonSchema) -> JsonSchema:
    """`z.union([...])` and `z.discriminatedUnion(key, [...])`."""
    return {"anyOf": list(options)}


def req(schema: JsonSchema) -> Property:
    """A required property."""
    return schema, True


def opt(schema: JsonSchema) -> Property:
    """`.optional()`."""
    return schema, False


def obj(properties: dict[str, Property], *, passthrough: bool = False) -> JsonSchema:
    """`z.object({...})`, `.passthrough()` keeping unknown keys."""
    schema: JsonSchema = {"type": "object", "properties": {name: s for name, (s, _) in properties.items()}}
    required = [name for name, (_, is_required) in properties.items() if is_required]
    if required:
        schema["required"] = required
    if passthrough:
        schema["additionalProperties"] = {}
    return schema


def document(schema: JsonSchema) -> JsonSchema:
    """The root of `toJSONSchema`'s output: the draft it targets, then the schema."""
    return {"$schema": JSON_SCHEMA_DRAFT, **schema}


__all__ = [
    "JSON_SCHEMA_DRAFT",
    "JsonSchema",
    "MAX_SAFE_INTEGER",
    "Property",
    "array",
    "boolean",
    "describe",
    "document",
    "enum",
    "integer",
    "literal",
    "number",
    "obj",
    "opt",
    "record",
    "req",
    "string",
    "union",
]
