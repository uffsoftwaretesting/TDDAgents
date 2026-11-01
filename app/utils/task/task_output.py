"""
A shell command's output file and what is read back from it.

Ported from `reference/claude-code/src/utils/task/TaskOutput.ts` in file mode (Bash): the child
writes stdout and stderr straight to `<task dir>/<task id>.output`; `get_stdout` reads the first
`get_max_output_length()` bytes, and records whether that was everything
(`output_file_redundant`, so a small file can be deleted) and how large the file is. stderr
is part of the same file, so `get_stderr` is empty. Not ported: the shared progress poller and
pipe mode (hooks keep `app/utils/shell_command.py::CapturedProcess`).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from app.utils.shell.output_limits import get_max_output_length


class TaskOutput:
    """The output file of one shell command (`TaskOutput`, file mode)."""

    def __init__(self, task_id: str, path: Path) -> None:
        self.task_id = task_id
        self.path = path
        self._output_file_redundant = False
        self._output_file_size = 0

    @property
    def output_file_redundant(self) -> bool:
        """The whole file fit in what `get_stdout` returned."""
        return self._output_file_redundant

    @property
    def output_file_size(self) -> int:
        return self._output_file_size

    def get_stdout(self) -> str:
        """`#readStdoutFromFile`: the head of the file, `<bash output unavailable: …>` on error."""
        limit = get_max_output_length()
        try:
            with self.path.open("rb") as handle:
                data = handle.read(limit)
            total = os.path.getsize(self.path)
        except OSError as exc:
            return f"<bash output unavailable: {exc}>"
        self._output_file_size = total
        self._output_file_redundant = total <= len(data)
        return data.decode("utf-8", errors="replace")

    def get_stderr(self) -> str:
        """File mode: stderr went to the same file as stdout."""
        return ""

    def delete_output_file(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()
