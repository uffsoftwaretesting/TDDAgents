"""
LocalWorkspace — the Workspace protocol over the developer's host filesystem.

Full parity with E2BWorkspace, including `execute()`. This is deliberate and the
trade-off was accepted when the two-execution-target design was locked: there is no
per-call override and no command allowlist, so the only thing standing between an
agent and an arbitrary host command is the `workspace:` value in its definition file.
The mitigation is structural — tester, developer, and refactorer are pinned to
`sandbox`, and the registry is what enforces it.

Generated code still only ever runs in the sandbox.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from app.config.config import Config
from app.workspace.base import (
    CommandResult,
    FileEntry,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceTimeout,
    normalize_path,
)

logger = logging.getLogger("TDDOrchestrator.LocalWorkspace")


class LocalWorkspace:
    """A Workspace rooted at a single directory on the host."""

    kind = "local"

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(cls, thread_id: str) -> "LocalWorkspace":
        """
        The local side of a run, at
        `<Config.LOCAL_WORKSPACE_ROOT>/<thread_id>/workspace`.

        This is the live mirror the sync engine writes to. It is NOT
        `workspace_output_<thread_id>/`, which stays exactly what it is today: the
        end-of-run export written by main.py, so the paper's artifact directory never
        contains a half-synced tree.
        """
        return cls(Path(Config.LOCAL_WORKSPACE_ROOT) / thread_id / "workspace")

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str) -> Path:
        """
        Turns a workspace-relative path into an absolute host path.

        `normalize_path` already rejects `..` escapes; the containment check below is
        the second line of defense, catching an escape smuggled in through a symlink.
        """
        relative = normalize_path(path)
        candidate = self._root if relative == "." else self._root / relative

        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise WorkspacePathError(f"Could not resolve path '{path}': {exc}") from exc

        if resolved != self._root and self._root not in resolved.parents:
            raise WorkspacePathError(f"Path '{path}' escapes the workspace root.")

        return candidate

    # ── Files ────────────────────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        target = self.resolve(path)
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise WorkspaceNotFound(f"File not found: {path}") from exc
        except IsADirectoryError as exc:
            raise WorkspacePathError(f"Path is a directory, not a file: {path}") from exc
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not read '{path}': {exc}") from exc

    def write_file(self, path: str, content: str) -> None:
        target = self.resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not write '{path}': {exc}") from exc

    def delete_file(self, path: str) -> None:
        target = self.resolve(path)
        if not target.exists() and not target.is_symlink():
            raise WorkspaceNotFound(f"File not found: {path}")
        try:
            if target.is_dir() and not target.is_symlink():
                import shutil

                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not delete '{path}': {exc}") from exc

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        target = self.resolve(path)
        if not target.exists():
            raise WorkspaceNotFound(f"Directory not found: {path}")
        if not target.is_dir():
            raise WorkspacePathError(f"Path is not a directory: {path}")

        entries: list[FileEntry] = []
        for current, dirnames, filenames in os.walk(target):
            current_path = Path(current)
            # Depth is counted in entries, matching E2B: depth=1 is immediate children.
            child_depth = len(current_path.relative_to(target).parts) + 1

            if child_depth > depth:
                dirnames[:] = []
                continue

            children = sorted(dirnames) + sorted(filenames)
            if child_depth == depth:
                dirnames[:] = []  # emit this level, then stop descending

            for name in children:
                entry = current_path / name
                entries.append(
                    FileEntry(
                        path=entry.relative_to(self._root).as_posix(),
                        is_dir=entry.is_dir(),
                        size=entry.stat().st_size if entry.is_file() else 0,
                    )
                )

        return sorted(entries, key=lambda e: e.path)

    def exists(self, path: str) -> bool:
        try:
            return self.resolve(path).exists()
        except WorkspacePathError:
            return False

    def move(self, old: str, new: str) -> None:
        source = self.resolve(old)
        destination = self.resolve(new)
        if not source.exists():
            raise WorkspaceNotFound(f"File not found: {old}")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not move '{old}' to '{new}': {exc}") from exc

    # ── Execution ────────────────────────────────────────────────────────────

    def execute(
        self,
        cmd: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """
        Runs a shell command from the workspace root.

        Invoked as `/bin/bash -l -c <cmd>` to match exactly how E2B starts commands
        (`e2b/sandbox_sync/commands/command.py`), so a command behaves the same on
        both sides. As in the sandbox, a non-zero exit code is returned, not raised.
        """
        limit = timeout if timeout is not None else Config.COMMAND_TIMEOUT
        environment = {**os.environ, **(env or {})}
        started = time.monotonic()

        try:
            completed = subprocess.run(
                ["/bin/bash", "-l", "-c", cmd],
                cwd=str(self._root),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceTimeout(
                f"Local command exceeded {limit}s: {cmd}"
            ) from exc
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not run local command '{cmd}': {exc}") from exc

        return CommandResult(
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
            duration=time.monotonic() - started,
            workspace="local",
        )
