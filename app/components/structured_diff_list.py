"""
A list of diff hunks, one after the other.

Ported from `reference/claude-code/src/components/StructuredDiffList.tsx` -> `StructuredDiffList`:
each hunk through `StructuredDiff` (here its fallback layout, `app/components/structured_diff/
fallback.py`), with a `...` line between two hunks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.components.structured_diff.fallback import format_diff

#: The separator `intersperse` puts between two hunks.
HUNK_SEPARATOR = "..."


def structured_diff_list(hunks: Sequence[Mapping[str, Any]]) -> list[str]:
    """The hunks' numbered lines, `HUNK_SEPARATOR` between two hunks."""
    lines: list[str] = []
    for index, hunk in enumerate(hunks):
        if index:
            lines.append(HUNK_SEPARATOR)
        lines += format_diff(hunk.get("lines") or [], int(hunk.get("oldStart") or 0))
    return lines


__all__ = ["HUNK_SEPARATOR", "structured_diff_list"]
