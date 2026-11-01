"""
How much of a shell command's output a tool result keeps.

Ported from `reference/claude-code/src/utils/shell/outputLimits.ts` -> `BASH_MAX_OUTPUT_DEFAULT`,
`BASH_MAX_OUTPUT_UPPER_LIMIT`, `getMaxOutputLength` (`BASH_MAX_OUTPUT_LENGTH`).
"""

from __future__ import annotations

import os

BASH_MAX_OUTPUT_DEFAULT = 30_000
BASH_MAX_OUTPUT_UPPER_LIMIT = 150_000


def get_max_output_length() -> int:
    """`getMaxOutputLength`: `BASH_MAX_OUTPUT_LENGTH`, bounded, else the default."""
    raw = os.environ.get("BASH_MAX_OUTPUT_LENGTH")
    try:
        value = int(raw) if raw is not None else BASH_MAX_OUTPUT_DEFAULT
    except ValueError:
        return BASH_MAX_OUTPUT_DEFAULT
    if value <= 0:
        return BASH_MAX_OUTPUT_DEFAULT
    return min(value, BASH_MAX_OUTPUT_UPPER_LIMIT)
