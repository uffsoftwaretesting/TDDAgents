"""
String helpers.

Ported from `reference/claude-code/src/utils/stringUtils.ts` -> `plural`.
"""

from __future__ import annotations


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    """`plural`: `word` for exactly one, else `plural_word` (default `word + 's'`)."""
    return word if n == 1 else (plural_word if plural_word is not None else f"{word}s")


__all__ = ["plural"]
