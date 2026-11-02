"""A scripted `DialogIO` (`app/components/design_system/dialog.py`): the lines a user would type, `ESC` for Esc."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

ESC = object()


class FakeDialog:
    """Answers each question with the next scripted line; a line the question rejects is followed by the next."""

    def __init__(self, *lines: Any) -> None:
        self.lines: list[Any] = list(lines)
        self.out: list[str] = []
        self.asked: list[str] = []

    def write(self, text: str) -> None:
        self.out.append(text)

    async def ask(self, text: str, parse: Callable[[str], Any], retry: str, cancelled: Any) -> Any:
        self.out.append(text)
        self.asked.append(text)
        while True:
            if not self.lines:
                await asyncio.Event().wait()  # the user types nothing more
            line = self.lines.pop(0)
            if line is ESC:
                return cancelled
            answer = parse(line)
            if answer is not None:
                return answer
            self.out.append(retry)

    def text(self) -> str:
        return "\n".join(self.out)
