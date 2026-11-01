"""
E2BWorkspace — the Workspace protocol backed by the remote E2B sandbox.

Deliberately thin: every operation is one call into E2BAdapter, which already owns
path resolution, exception translation, and the sandbox lifetime refresh. Keeping the
logic there and not here is what makes LocalWorkspace a peer implementation rather
than a special case.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

from app.sandbox.adapter import BackgroundCommand, E2BAdapter
from app.workspace.base import CommandResult, FileEntry, WorkspaceKind

#: How often a sandbox command's buffered output is copied into its output file.
OUTPUT_POLL_SECONDS = 0.2


class E2BShellProcess:
    """
    A sandbox command started in the background. Its output arrives through the SDK's callbacks
    into the handle's buffers; `wait` copies them into the output file until the command exits.
    """

    def __init__(self, adapter: E2BAdapter, background: BackgroundCommand, output_fd: int) -> None:
        self._adapter = adapter
        self._background = background
        self._fd = output_fd

    @property
    def pid(self) -> int | None:
        return self._background.pid

    def _copy_output(self) -> None:
        stdout, stderr = self._background.drain()
        for chunk in (stdout, stderr):
            if chunk:
                os.write(self._fd, chunk.encode("utf-8"))

    async def wait(self) -> int:
        waiter = asyncio.ensure_future(asyncio.to_thread(_wait_for_exit, self._background))
        try:
            while not waiter.done():
                self._copy_output()
                await asyncio.wait({waiter}, timeout=OUTPUT_POLL_SECONDS)
            code = waiter.result()
            self._copy_output()
            return code
        finally:
            with contextlib.suppress(OSError):
                os.close(self._fd)

    def kill_tree(self) -> None:
        self._adapter.kill_command(self._background.id)


def _wait_for_exit(background: BackgroundCommand) -> int:
    """The SDK handle's `wait()`: a non-zero exit raises, carrying its `exit_code`."""
    try:
        result = background.handle.wait()
    except Exception as exc:  # noqa: BLE001 - the SDK signals a non-zero exit (and a kill) by raising
        return int(getattr(exc, "exit_code", 1) or 1)
    return int(getattr(result, "exit_code", 0) or 0)


class E2BWorkspace:
    """A Workspace over one E2B sandbox."""

    kind: WorkspaceKind = "sandbox"

    def __init__(self, adapter: E2BAdapter) -> None:
        self._adapter = adapter

    @classmethod
    def for_sandbox(cls, sandbox_id: str) -> E2BWorkspace:
        """Convenience constructor for callers holding only an id."""
        return cls(E2BAdapter.connect(sandbox_id))

    @property
    def adapter(self) -> E2BAdapter:
        return self._adapter

    @property
    def sandbox_id(self) -> str:
        return self._adapter.sandbox_id

    def read_file(self, path: str) -> str:
        return self._adapter.read(path)

    def write_file(self, path: str, content: str) -> None:
        self._adapter.write(path, content)

    def delete_file(self, path: str) -> None:
        self._adapter.remove(path)

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        return self._adapter.list(path, depth=depth)

    def exists(self, path: str) -> bool:
        return self._adapter.exists(path)

    def move(self, old: str, new: str) -> None:
        self._adapter.rename(old, new)

    def execute(
        self,
        cmd: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        return self._adapter.execute(cmd, timeout=timeout, env=env)

    async def spawn(
        self, cmd: str, *, output_fd: int, cwd: str | None = None, cwd_file: str | None = None
    ) -> E2BShellProcess:
        """
        A background sandbox command (`E2BAdapter.start_background`) whose output is copied into
        `output_fd` as it arrives. The sandbox's paths are not host paths, so the working
        directory is not tracked here: `cwd_file` is ignored and the command runs from `cwd`.
        """
        background = await asyncio.to_thread(self._adapter.start_background, cmd, cwd)
        return E2BShellProcess(self._adapter, background, os.dup(output_fd))
