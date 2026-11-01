"""
Grapheme segmentation.

Ported from `reference/claude-code/src/utils/intl.ts` -> `getGraphemeSegmenter`, `firstGrapheme`,
`lastGrapheme`. `Intl.Segmenter`'s grapheme granularity is the `regex` package's `\\X` (extended
grapheme clusters, UAX #29). The word segmenter and the cached time-zone/relative-time formatters
have no caller here.
"""

from __future__ import annotations

import regex

_GRAPHEME = regex.compile(r"\X")


def graphemes(text: str) -> list[str]:
    """`getGraphemeSegmenter().segment(text)`, as the segments' strings."""
    return _GRAPHEME.findall(text)


def first_grapheme(text: str) -> str:
    """`firstGrapheme`: the first grapheme cluster, `""` for an empty string."""
    match = _GRAPHEME.match(text)
    return match.group(0) if match else ""


def last_grapheme(text: str) -> str:
    """`lastGrapheme`: the last grapheme cluster, `""` for an empty string."""
    found = graphemes(text)
    return found[-1] if found else ""


__all__ = ["first_grapheme", "graphemes", "last_grapheme"]
