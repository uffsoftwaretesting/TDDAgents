"""
MCPB user configuration: the value and schema types and their validation.

Ported from `reference/claude-code/src/utils/plugins/mcpbHandler.ts` -> `UserConfigValues`,
`UserConfigSchema`, `validateUserConfig`.

Only what plugin options need is ported here (stage 1). The rest of the MCPB handler (download,
extraction, the content-hash cache, `loadMcpbFile`, `saveMcpServerUserConfig`) belongs to stage 2.
`McpbUserConfigurationOption` (from `@anthropic-ai/mcpb`) has the same shape as the manifest's
`PluginUserConfigOption`, so that schema stands in for it, as upstream's comment intends.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from app.utils.plugins.schemas import PluginUserConfigOption

#: `UserConfigValues`.
UserConfigValues: TypeAlias = dict[str, str | float | bool | list[str]]
#: `UserConfigSchema`.
UserConfigSchema: TypeAlias = dict[str, PluginUserConfigOption]


@dataclass(frozen=True)
class UserConfigValidation:
    """`validateUserConfig`'s `{valid, errors}`."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _js_number(value: float) -> str:
    """`${n}` for a JS number: integral floats print without `.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


_ABSENT = object()


def validate_user_config(
    values: Mapping[str, Any], schema: Mapping[str, Mapping[str, Any]]
) -> UserConfigValidation:
    """`validateUserConfig`: every field of the schema checked against the values."""
    errors: list[str] = []
    for key, field_schema in schema.items():
        # `undefined` is an absent key; a stored `null` is a value and fails the type check.
        value = values.get(key, _ABSENT)
        absent = value is _ABSENT or (isinstance(value, str) and value == "")
        label = field_schema.get("title") or key
        if field_schema.get("required") and absent:
            errors.append(f"{label} is required but not provided")
            continue
        if absent:
            continue
        kind = field_schema.get("type")
        if kind == "string":
            if isinstance(value, list):
                if not field_schema.get("multiple"):
                    errors.append(f"{label} must be a string, not an array")
                elif not all(isinstance(v, str) for v in value):
                    errors.append(f"{label} must be an array of strings")
            elif not isinstance(value, str):
                errors.append(f"{label} must be a string")
        elif kind == "number" and not _is_number(value):
            errors.append(f"{label} must be a number")
        elif kind == "boolean" and not isinstance(value, bool):
            errors.append(f"{label} must be a boolean")
        elif kind in ("file", "directory") and not isinstance(value, str):
            errors.append(f"{label} must be a path string")
        if kind == "number" and _is_number(value):
            minimum = field_schema.get("min")
            maximum = field_schema.get("max")
            if minimum is not None and value < minimum:
                errors.append(f"{label} must be at least {_js_number(minimum)}")
            if maximum is not None and value > maximum:
                errors.append(f"{label} must be at most {_js_number(maximum)}")
    return UserConfigValidation(valid=not errors, errors=errors)


__all__ = ["UserConfigSchema", "UserConfigValidation", "UserConfigValues", "validate_user_config"]
