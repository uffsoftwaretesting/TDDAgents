"""
PDF page ranges and extensions.

Ported from `reference/claude-code/src/utils/pdfUtils.ts`: `DOCUMENT_EXTENSIONS`,
`parsePDFPageRange`, `isPDFSupported`, `isPDFExtension`.
"""

from __future__ import annotations

import math
import re

DOCUMENT_EXTENSIONS = frozenset({"pdf"})

_LEADING_INT = re.compile(r"^\s*([+-]?\d+)")


def _parse_int(text: str) -> int | None:
    """JavaScript `parseInt(text, 10)`: the leading integer, or `None` (NaN)."""
    match = _LEADING_INT.match(text)
    return int(match.group(1)) if match else None


def parse_pdf_page_range(pages: str) -> tuple[int, float] | None:
    """
    `parsePDFPageRange`: `"5"` → (5, 5), `"1-10"` → (1, 10), `"3-"` → (3, ∞). `None` on invalid
    input (non-numeric, zero, inverted range). Pages are 1-indexed.
    """
    trimmed = pages.strip()
    if not trimmed:
        return None
    if trimmed.endswith("-"):
        first = _parse_int(trimmed[:-1])
        if first is None or first < 1:
            return None
        return first, math.inf
    dash = trimmed.find("-")
    if dash == -1:
        page = _parse_int(trimmed)
        if page is None or page < 1:
            return None
        return page, page
    first, last = _parse_int(trimmed[:dash]), _parse_int(trimmed[dash + 1:])
    if first is None or last is None or first < 1 or last < 1 or last < first:
        return None
    return first, last


def is_pdf_supported(model: str) -> bool:
    """`isPDFSupported`: every model but Claude 3 Haiku reads PDF document blocks."""
    return "claude-3-haiku" not in model.lower()


def is_pdf_extension(ext: str) -> bool:
    """`isPDFExtension`, with or without the leading dot."""
    return ext.removeprefix(".").lower() in DOCUMENT_EXTENSIONS


__all__ = ["DOCUMENT_EXTENSIONS", "is_pdf_extension", "is_pdf_supported", "parse_pdf_page_range"]
