"""
The settings schema as JSON text.

Ported from `reference/claude-code/src/utils/settings/schemaOutput.ts` ->
`generateSettingsJSONSchema`, and `src/skills/bundled/updateConfig.ts` -> `generateSettingsSchema`
(the same document, printed at the end of the `update-config` skill's prompt as
"Full Settings JSON Schema").

`jsonStringify(jsonSchema, null, 2)`: two-space indentation, non-ASCII text as is. The schema is
`app/utils/settings/types.py` -> `settings_json_schema`, the keys this build reads.
"""

from __future__ import annotations

import json

from app.utils.settings.types import settings_json_schema


def generate_settings_json_schema() -> str:
    """`generateSettingsJSONSchema` / `generateSettingsSchema`."""
    return json.dumps(settings_json_schema(), indent=2, ensure_ascii=False)


__all__ = ["generate_settings_json_schema"]
