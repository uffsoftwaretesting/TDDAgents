"""
The text-mode stand-in for upstream's dialog primitives: a titled block of lines, and a
numbered choice read from the next line typed.

Ported in shape from `reference/claude-code/src/components/design-system/Dialog.tsx` (a title,
a subtitle, the body, Esc cancels) and `components/CustomSelect/select.tsx` (`Select`: options,
`onChange`, `onCancel`). Ink renders a focused list moved with ↑↓ and confirmed with Enter; the
REPL reads lines (`app/screens/repl.py`), so an option is picked by its number, and Esc answers the
question with the dialog's cancel value.

`DialogIO` is what a dialog needs from the REPL: `write` (a block of text above the input line)
and `ask` (show a question, then read the next line through `parse`; a line `parse` maps to
`None` is answered with `retry`; Esc answers `cancelled`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol


class DialogIO(Protocol):
    """The terminal as a dialog sees it."""

    def write(self, text: str) -> None:
        """Show `text` above the input line."""

    async def ask(self, text: str, parse: Callable[[str], Any], retry: str, cancelled: Any) -> Any:
        """Show `text`; the next line read through `parse`, or `cancelled` on Esc."""


class Cancelled:
    """The answer to a dialog the user left with Esc."""

    def __repr__(self) -> str:
        return "CANCELLED"


CANCELLED = Cancelled()


def parse_choice(count: int, line: str) -> int | None:
    """`2` picks the second of `count` options (index 1); anything else is `None`."""
    text = line.strip()
    if not text.isdigit():
        return None
    index = int(text) - 1
    return index if 0 <= index < count else None


def render_options(labels: Sequence[str], descriptions: Sequence[str | None] | None = None) -> list[str]:
    """`Select`'s options, numbered from 1; an option's description follows a dash."""
    described = descriptions or [None] * len(labels)
    return [f"  {i}. {label}" + (f" — {description}" if description else "")
            for i, (label, description) in enumerate(zip(labels, described, strict=True), start=1)]


def choice_hint(count: int, escape_action: str) -> str:
    """The footer under a choice: which numbers pick, and what Esc does."""
    return f"Choose 1-{count} · Esc to {escape_action}"


async def select(
    io: DialogIO,
    lines: Sequence[str],
    labels: Sequence[str],
    escape_action: str,
    descriptions: Sequence[str | None] | None = None,
) -> int | Cancelled:
    """`<Select>` under `lines`: the picked option's index, or `CANCELLED` on Esc."""
    text = "\n".join([*lines, *render_options(labels, descriptions), choice_hint(len(labels), escape_action)])
    answer: int | Cancelled = await io.ask(
        text, lambda line: parse_choice(len(labels), line), f"Choose 1-{len(labels)}.", CANCELLED)
    return answer


__all__ = [
    "CANCELLED",
    "Cancelled",
    "DialogIO",
    "choice_hint",
    "parse_choice",
    "render_options",
    "select",
]
