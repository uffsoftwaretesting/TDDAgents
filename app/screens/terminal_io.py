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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Self

from prompt_toolkit import PromptSession
from prompt_toolkit.history import History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout

from app.history import PromptHistory


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

    def __enter__(self) -> Self:
        self._patch.__enter__()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._patch.__exit__(exc_type, exc, tb)

    async def read_line(self, prompt: str) -> str | None:
        while True:
            try:
                line: str = await self._session.prompt_async(prompt)
                return line
            except KeyboardInterrupt:
                self._on_interrupt()
                print("(Esc or Ctrl-C interrupts the agents; /exit leaves)", flush=True)
            except EOFError:
                return None

    def write(self, text: str) -> None:
        print(text, flush=True)
