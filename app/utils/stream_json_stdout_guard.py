"""
Keep stdout pure NDJSON under `--output-format stream-json`.

Ported from `reference/claude-code/src/utils/streamJsonStdoutGuard.ts` ->
`installStreamJsonStdoutGuard`, `STDOUT_GUARD_MARKER`: writes to stdout are buffered to the
newline, and each complete line that is not JSON goes to stderr behind the marker instead, so a
stray `print` never breaks an SDK client's parser. An empty line counts as JSON. Uninstalling
flushes what is left (upstream's `registerCleanup`).
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

#: `STDOUT_GUARD_MARKER`.
STDOUT_GUARD_MARKER = "[stdout-guard]"


def is_json_line(line: str) -> bool:
    """`isJsonLine`."""
    if not line:
        return True
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


class GuardedStdout:
    """The wrapped `process.stdout.write`."""

    def __init__(self, original: TextIO, stderr: TextIO) -> None:
        self.original = original
        self.stderr = stderr
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        while (newline := self.buffer.find("\n")) != -1:
            line, self.buffer = self.buffer[:newline], self.buffer[newline + 1:]
            if is_json_line(line):
                self.original.write(line + "\n")
            else:
                self.stderr.write(f"{STDOUT_GUARD_MARKER} {line}\n")
        return len(text)

    def flush(self) -> None:
        self.original.flush()

    def drain(self) -> None:
        """The cleanup: a last unterminated line goes where a whole one would."""
        if self.buffer:
            if is_json_line(self.buffer):
                self.original.write(self.buffer + "\n")
            else:
                self.stderr.write(f"{STDOUT_GUARD_MARKER} {self.buffer}\n")
            self.buffer = ""
        self.original.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


def install_stream_json_stdout_guard() -> GuardedStdout:
    """`installStreamJsonStdoutGuard`; installing twice returns the guard already in place."""
    if isinstance(sys.stdout, GuardedStdout):
        return sys.stdout
    guard = GuardedStdout(sys.stdout, sys.stderr)
    sys.stdout = guard
    return guard


def uninstall_stream_json_stdout_guard() -> None:
    """The cleanup `installStreamJsonStdoutGuard` registers: flush, then the real stdout back."""
    current: Any = sys.stdout
    if isinstance(current, GuardedStdout):
        current.drain()
        sys.stdout = current.original


__all__ = [
    "STDOUT_GUARD_MARKER",
    "GuardedStdout",
    "install_stream_json_stdout_guard",
    "is_json_line",
    "uninstall_stream_json_stdout_guard",
]
