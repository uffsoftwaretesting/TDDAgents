"""
The terminal under the REPL: `prompt_toolkit`'s input line, kept live while output streams
above it (Phase 11).

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.** This
module is the boundary to the terminal: every line resolves to `prompt_toolkit` reading a real
TTY, and there is no seam beneath it to fake. It is verified by a static surface check
(`tests/test_terminal_io_surface.py`) and by running the REPL end to end. Everything above it
(`app/screens/repl.py`) talks to it through `ReplIO` and is tested against a fake.

Esc interrupts the agents (upstream `chat:cancel`); Ctrl-C does too while they work, and is
otherwise ignored with a hint; Ctrl-D leaves. ↑ walks the prompt history (`app/history.py`, kept
across sessions per project) through a prompt_toolkit `History` over it.

**The status line** (`set_status`, the REPL's `status_line`) is drawn above the input line, as part of
the prompt, and looked at once every `STATUS_REFRESH_SECONDS`: the prompt is redrawn only when the line
changed (user decision 2026-11-14). A recorded session showed that every redraw hides the cursor, erases
the prompt and paints it again, so an animated line would make the input line blink.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Self

from prompt_toolkit import PromptSession
from prompt_toolkit.history import History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout

from app.history import PromptHistory
from app.utils.debug import route_terminal_logs

#: How often the status line is looked at (a redraw follows only when it changed).
STATUS_REFRESH_SECONDS = 1.0


class PromptToolkitHistory(History):
    """prompt_toolkit's `History` over `PromptHistory`: loaded newest first, appended on submit."""

    def __init__(self, history: PromptHistory) -> None:
        super().__init__()
        self._history = history

    def load_history_strings(self) -> Iterable[str]:
        return self._history.get_history()

    def store_string(self, string: str) -> None:
        self._history.add_to_history(string)


class TerminalIO:
    """`ReplIO` on a real terminal."""

    def __init__(self, on_interrupt: Callable[[], None], history: PromptHistory | None = None) -> None:
        self._on_interrupt = on_interrupt
        bindings = KeyBindings()

        @bindings.add("escape", eager=True)
        def _escape(event: KeyPressEvent) -> None:
            self._on_interrupt()

        self._session: PromptSession[str] = PromptSession(
            key_bindings=bindings,
            history=PromptToolkitHistory(history) if history is not None else InMemoryHistory())
        self._patch = patch_stdout(raw=True)
        self._restore_logs: Callable[[], None] = lambda: None
        self._status: Callable[[], str | None] = lambda: None
        self._ticker: asyncio.Task[None] | None = None

    def set_status(self, status: Callable[[], str | None]) -> None:
        """The line to show above the input while the agents work; `None` shows none."""
        self._status = status

    def _message(self, prompt: str) -> Callable[[], str]:
        def message() -> str:
            line = self._status()
            return f"{line}\n{prompt}" if line else prompt
        return message

    async def _refresh_status(self) -> None:
        shown = self._status()
        while True:
            await asyncio.sleep(STATUS_REFRESH_SECONDS)
            current = self._status()
            if current != shown:
                shown = current
                if self._session.app.is_running:
                    self._session.app.invalidate()

    def __enter__(self) -> Self:
        self._patch.__enter__()
        # The log records that still reach the terminal go above the input line, as print() does.
        self._restore_logs = route_terminal_logs(sys.stderr)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None
        self._restore_logs()
        self._patch.__exit__(exc_type, exc, tb)

    async def read_line(self, prompt: str) -> str | None:
        if self._ticker is None:
            self._ticker = asyncio.ensure_future(self._refresh_status())
        while True:
            try:
                line: str = await self._session.prompt_async(self._message(prompt))
                return line
            except KeyboardInterrupt:
                self._on_interrupt()
                print("(Esc or Ctrl-C interrupts the agents; /exit leaves)", flush=True)
            except EOFError:
                return None

    def write(self, text: str) -> None:
        print(text, flush=True)
