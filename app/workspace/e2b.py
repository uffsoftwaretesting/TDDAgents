"""
E2BWorkspace — the Workspace protocol backed by the remote E2B sandbox.

Deliberately thin: every operation is one call into E2BAdapter, which already owns
path resolution, exception translation, and the sandbox lifetime refresh. Keeping the
logic there and not here is what makes LocalWorkspace a peer implementation rather
than a special case.
"""

from __future__ import annotations

from app.sandbox.adapter import E2BAdapter
from app.workspace.base import CommandResult, FileEntry


class E2BWorkspace:
    """A Workspace over one E2B sandbox."""

    kind = "sandbox"

    def __init__(self, adapter: E2BAdapter) -> None:
        self._adapter = adapter

    @classmethod
    def for_sandbox(cls, sandbox_id: str) -> "E2BWorkspace":
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
