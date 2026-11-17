"""
The one place the REPL turns a rich renderable into the ANSI text it writes.

TDDAgents' own (the port's stand-in for Ink rendering a component): `to_ansi` renders through a
`rich.console.Console` writing to a string, with markup and emoji codes off, so text the model wrote
(`[Perf size]`, `:smile:`) is shown as it is; `plain_text` is what a renderable reads as without styles.
The REPL writes the styled text on a terminal that shows colour (`TerminalIO.supports_color`) and the
plain text everywhere else.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console
from rich.text import Text


def to_ansi(renderable: Any, width: int) -> str:
    """`renderable` as ANSI text `width` columns wide, without its trailing newline."""
    out = io.StringIO()
    Console(file=out, force_terminal=True, color_system="truecolor", width=width, highlight=False,
            markup=False, emoji=False).print(renderable)
    return out.getvalue().rstrip("\n")


def plain_text(ansi: str) -> str:
    """What ANSI text reads as with its styles removed."""
    return Text.from_ansi(ansi).plain


__all__ = ["plain_text", "to_ansi"]
