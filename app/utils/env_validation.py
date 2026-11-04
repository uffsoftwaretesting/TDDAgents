"""
A bounded integer environment variable: the value in effect, and whether it was taken as given.

Ported from `reference/claude-code/src/utils/envValidation.ts` -> `EnvVarValidationResult`,
`validateBoundedIntEnvVar`. `/doctor` lists the variables whose value is not taken as given
(`app/screens/doctor.py`); the readers of each variable keep their own bounded parse
(`app/utils/shell/output_limits.py`, `app/utils/task/disk_output.py`, `app/utils/tokens.py`), which
gives the same `effective` value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.utils.debug import log_for_debugging

#: What `parseInt(value, 10)` reads: optional blanks and sign, then digits.
_LEADING_INT = re.compile(r"^\s*([+-]?\d+)")


@dataclass(frozen=True, slots=True)
class EnvVarValidationResult:
    """`EnvVarValidationResult`."""

    effective: int
    status: Literal["valid", "capped", "invalid"]
    message: str | None = None


def validate_bounded_int_env_var(
    name: str, value: str | None, default_value: int, upper_limit: int,
) -> EnvVarValidationResult:
    """`validateBoundedIntEnvVar`: the default when unset, invalid or not positive; capped at `upper_limit`."""
    if not value:
        return EnvVarValidationResult(default_value, "valid")
    match = _LEADING_INT.match(value)
    parsed = int(match.group(1)) if match else None
    if parsed is None or parsed <= 0:
        result = EnvVarValidationResult(default_value, "invalid",
                                        f'Invalid value "{value}" (using default: {default_value})')
        log_for_debugging(f"{name} {result.message}")
        return result
    if parsed > upper_limit:
        result = EnvVarValidationResult(upper_limit, "capped", f"Capped from {parsed} to {upper_limit}")
        log_for_debugging(f"{name} {result.message}")
        return result
    return EnvVarValidationResult(parsed, "valid")


__all__ = ["EnvVarValidationResult", "validate_bounded_int_env_var"]
