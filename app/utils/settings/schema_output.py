"""
The settings schema as JSON text, in Zod's two modes.

Ported from `reference/claude-code/src/utils/settings/schemaOutput.ts` -> `generateSettingsJSONSchema`
(`toJSONSchema(SettingsSchema(), {unrepresentable: 'any'})`, the output mode: the schema shown with a
refused settings edit, `validateSettingsFileContent`'s `fullSchema`) and
`src/skills/bundled/updateConfig.ts` -> `generateSettingsSchema` (`{io: 'input'}`: the "Full Settings JSON
Schema" at the end of the `update-config` skill's prompt).

The two differ as Zod's modes do: the output mode closes every object that is not `.passthrough()` and
not a record (`"additionalProperties": false`, written last), since a parsed value has its unknown keys
stripped; the input mode leaves them open. `jsonStringify(jsonSchema, null, 2)`: two-space indentation,
non-ASCII text as is. The schema is `app/utils/settings/types.py` -> `settings_json_schema`.
"""

from __future__ import annotations

import json
from typing import Any

from app.utils.settings.types import settings_json_schema


def _output_mode(node: Any) -> Any:
    """The output mode of an input-mode schema: each closed object gets `"additionalProperties": false`."""
    if isinstance(node, list):
        return [_output_mode(item) for item in node]
    if not isinstance(node, dict):
        return node
    converted = {key: _output_mode(value) for key, value in node.items()}
    if node.get("type") == "object" and "properties" in node and "additionalProperties" not in node:
        converted["additionalProperties"] = False
    return converted


def generate_settings_json_schema() -> str:
    """`generateSettingsJSONSchema`: the output mode."""
    return json.dumps(_output_mode(settings_json_schema()), indent=2, ensure_ascii=False)


def generate_settings_schema() -> str:
    """`generateSettingsSchema` (`update-config`): the input mode."""
    return json.dumps(settings_json_schema(), indent=2, ensure_ascii=False)


__all__ = ["generate_settings_json_schema", "generate_settings_schema"]
