"""
The display width of a string in terminal columns.

Ported from `reference/claude-code/src/ink/stringWidth.ts` -> `stringWidthJavaScript` (the
implementation used when `Bun.stringWidth` is absent), `needsSegmentation`, `getEmojiWidth`,
`isZeroWidth`. `eastAsianWidth(cp, {ambiguousAsWide: false})` is `unicodedata.east_asian_width`:
Wide and Fullwidth are 2 columns, everything else (Ambiguous included) 1. `emoji-regex` is the
RGI-shaped `EMOJI_PATTERN` below (`regex` package properties), and `strip-ansi` is its ANSI
escape pattern.
"""

from __future__ import annotations

import unicodedata

import regex

from app.utils.intl import graphemes

#: `strip-ansi`'s pattern (`ansi-regex`), CSI/OSC and the other escape sequences.
ANSI_PATTERN = regex.compile(
    r"[\u001B\u009B][[\]()#;?]*(?:(?:(?:(?:;[-a-zA-Z\d/#&.:=?%@~_]+)*|[a-zA-Z\d]+(?:;[-a-zA-Z\d/#&.:=?%@~_]*)*)?"
    r"(?:\u0007|\u001B\\|\u009C))|(?:(?:\d{1,4}(?:;\d{0,4})*)?[\dA-PR-TZcf-nq-uy=><~]))"
)

#: `emojiRegex()` (RGI emoji), for one grapheme: an emoji-presentation character, a pictograph
#: made emoji by U+FE0F, a regional indicator, or a keycap. A bare text-presentation symbol such
#: as U+26A0 is not one, so it stays 1 column.
EMOJI_PATTERN = regex.compile(
    r"\p{Emoji_Presentation}|\p{Extended_Pictographic}️|[\U0001F1E6-\U0001F1FF]|[0-9#*]️?⃣"
)


def strip_ansi(text: str) -> str:
    """`stripAnsi`."""
    return ANSI_PATTERN.sub("", text)


def east_asian_width(code_point: int) -> int:
    """`eastAsianWidth(codePoint, { ambiguousAsWide: false })`."""
    return 2 if unicodedata.east_asian_width(chr(code_point)) in ("W", "F") else 1


def needs_segmentation(text: str) -> bool:
    """`needsSegmentation`: emoji, symbols, flags, variation selectors or a ZWJ."""
    for char in text:
        cp = ord(char)
        if 0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x1F1E6 <= cp <= 0x1F1FF:
            return True
        if 0xFE00 <= cp <= 0xFE0F or cp == 0x200D:
            return True
    return False


def get_emoji_width(grapheme: str) -> int:
    """`getEmojiWidth`: a lone regional indicator and a keycap are 1 column, every other emoji 2."""
    first = ord(grapheme[0])
    if 0x1F1E6 <= first <= 0x1F1FF:
        return 1 if len(grapheme) == 1 else 2
    if len(grapheme) == 2 and ord(grapheme[1]) == 0xFE0F and (0x30 <= first <= 0x39 or first in (0x23, 0x2A)):
        return 1
    return 2


def is_zero_width(cp: int) -> bool:
    """`isZeroWidth`."""
    if 0x20 <= cp < 0x7F:
        return False
    if 0xA0 <= cp < 0x0300:
        return cp == 0x00AD
    if cp <= 0x1F or 0x7F <= cp <= 0x9F:
        return True
    if 0x200B <= cp <= 0x200D or cp == 0xFEFF or 0x2060 <= cp <= 0x2064:
        return True
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return True
    if (0x0300 <= cp <= 0x036F or 0x1AB0 <= cp <= 0x1AFF or 0x1DC0 <= cp <= 0x1DFF or 0x20D0 <= cp <= 0x20FF
            or 0xFE20 <= cp <= 0xFE2F):
        return True
    if 0x0900 <= cp <= 0x0D4F:
        offset = cp & 0x7F
        if offset <= 0x03 or 0x3A <= offset <= 0x4F or 0x51 <= offset <= 0x57 or 0x62 <= offset <= 0x63:
            return True
    if (cp == 0x0E31 or 0x0E34 <= cp <= 0x0E3A or 0x0E47 <= cp <= 0x0E4E or cp == 0x0EB1
            or 0x0EB4 <= cp <= 0x0EBC or 0x0EC8 <= cp <= 0x0ECD):
        return True
    if 0x0600 <= cp <= 0x0605 or cp in (0x06DD, 0x070F, 0x08E2):
        return True
    return 0xD800 <= cp <= 0xDFFF or 0xE0000 <= cp <= 0xE007F


def string_width(text: str) -> int:
    """`stringWidth`: the columns `text` takes in a terminal."""
    if not text:
        return 0
    if all(ord(c) < 127 and c != "\x1b" for c in text):
        return sum(1 for c in text if ord(c) > 0x1F)
    if "\x1b" in text:
        text = strip_ansi(text)
        if not text:
            return 0
    if not needs_segmentation(text):
        return sum(east_asian_width(ord(c)) for c in text if not is_zero_width(ord(c)))
    width = 0
    for grapheme in graphemes(text):
        if EMOJI_PATTERN.match(grapheme):
            width += get_emoji_width(grapheme)
            continue
        visible = next((ord(c) for c in grapheme if not is_zero_width(ord(c))), None)
        if visible is not None:
            width += east_asian_width(visible)
    return width


__all__ = [
    "ANSI_PATTERN",
    "EMOJI_PATTERN",
    "east_asian_width",
    "get_emoji_width",
    "is_zero_width",
    "needs_segmentation",
    "string_width",
    "strip_ansi",
]
