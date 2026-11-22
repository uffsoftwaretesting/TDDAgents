"""
Ported from `reference/claude-code/src/utils/json.ts`: `safeParseJSON` (l.45-58) and `parseJSONUncached`
(l.31-40), with `utils/jsonRead.ts` `stripBOM`.

Strict `JSON.parse`: what does not parse is `None`, never a repaired guess. Upstream's memoization (an LRU of
50 small inputs) is a performance cache only and is not ported.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: `utils/jsonRead.ts` `UTF8_BOM`.
UTF8_BOM = "﻿"


def _reject_constant(name: str) -> Any:
    """`JSON.parse` has no `NaN` / `Infinity`; Python's decoder accepts them unless told not to."""
    raise ValueError(f"Unexpected token {name} in JSON")


def safe_parse_json(text: str | None, should_log_error: bool = True) -> Any:
    """`safeParseJSON`: the parsed value, or `None` for empty or invalid input (logged unless asked not to)."""
    if not text:
        return None
    try:
        return json.loads(text.removeprefix(UTF8_BOM), parse_constant=_reject_constant)
    except ValueError as error:
        if should_log_error:
            logger.error("%s", error)
        return None
