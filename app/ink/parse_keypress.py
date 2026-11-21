"""
SGR mouse reports.

Ported from `reference/claude-code/src/ink/parse-keypress.ts` -> `SGR_MOUSE_RE`, `ParsedMouse`, `parseMouseEvent`.
Only the mouse part is ported: prompt_toolkit parses keys itself and hands a mouse report over as the data of a
`Keys.Vt100MouseEvent` key press, which the interaction clock (`app/ink/components/app.py`) reads with this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `SGR_MOUSE_RE`: CSI < button ; col ; row M (press) or m (release).
SGR_MOUSE_RE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")


@dataclass(frozen=True, slots=True)
class ParsedMouse:
    """`ParsedMouse`: the raw SGR button code (low 2 bits the button, 0x20 motion, 0x40 wheel)."""

    button: int
    action: str
    col: int
    row: int
    sequence: str


def parse_mouse_event(sequence: str) -> ParsedMouse | None:
    """`parseMouseEvent`: an SGR report, or `None` for anything else and for a wheel event (it stays a key)."""
    match = SGR_MOUSE_RE.match(sequence)
    if match is None:
        return None
    button = int(match.group(1))
    if button & 0x40:
        return None
    return ParsedMouse(button=button, action="press" if match.group(4) == "M" else "release",
                       col=int(match.group(2)), row=int(match.group(3)), sequence=sequence)


__all__ = ["SGR_MOUSE_RE", "ParsedMouse", "parse_mouse_event"]
