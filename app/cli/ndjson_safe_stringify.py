"""
One JSON line that a line-splitting reader never breaks.

Ported from `reference/claude-code/src/cli/ndjsonSafeStringify.ts` -> `ndjsonSafeStringify`:
`jsonStringify`, then U+2028 and U+2029 (JavaScript line terminators, legal unescaped in JSON)
escaped. Non-ASCII text is kept as is, as `JSON.stringify` keeps it.
"""

from __future__ import annotations

import json
from typing import Any


def ndjson_safe_stringify(value: Any) -> str:
    """`ndjsonSafeStringify`: compact JSON, U+2028/U+2029 escaped."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


__all__ = ["ndjson_safe_stringify"]
