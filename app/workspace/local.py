"""
LocalWorkspace — the Workspace protocol over the developer's host filesystem.

Full parity with E2BWorkspace, including `execute()`. This is deliberate and the
trade-off was accepted when the two-execution-target design was locked: there is no
per-call override and no command allowlist, so the only thing standing between an
agent and an arbitrary host command is the `workspace:` value in its definition file.
Since Phase 13 every session's project has its own folder outside TDDAgents' own:
`<workspaces root>/<thread>/project`, with its own virtualenv at `.venv`
(`app/workspace/project.py`, `app/workspace/pyenv.py`), as claude-code works in the folder it
is opened in. Reports go beside it, in `<thread>/metrics_and_logging/`, outside the git tree.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from app.config.config import Config
from app.utils.session_environment import build_command
from app.workspace.base import (
    CommandResult,
    FileEntry,
    WorkspaceKind,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceTimeout,
    normalize_path,
)

logger = logging.getLogger("TDDOrchestrator.LocalWorkspace")


#: git stops its upward search for a repository at these directories.
GIT_CEILING_ENV = "GIT_CEILING_DIRECTORIES"

#: Where the session projects live, unless overridden (Phase 13, user decision).
WORKSPACES_DIR_ENV = "TDDAGENTS_WORKSPACES_DIR"
#: The project's folder inside a session's directory: the git working tree the model works in.
PROJECT_DIRNAME = "project"


def workspaces_root() -> Path:
    """`$TDDAGENTS_WORKSPACES_DIR`, else `<config home>/workspaces` (`~/.tddagents/workspaces`)."""
    from app.utils.tool_result_storage import config_home

    override = os.environ.get(WORKSPACES_DIR_ENV)
    return (Path(override) if override else config_home() / "workspaces").expanduser().resolve()


class LocalWorkspace:
    """A Workspace rooted at a single directory on the host."""

    kind: WorkspaceKind = "local"

    def __init__(self, root: str | os.PathLike[str], env: dict[str, str] | None = None) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # Merged into every command, under the per-call `env`: e.g. the session venv's PATH.
        self._env: dict[str, str] = dict(env or {})

    @property
    def run_dir(self) -> Path:
        """The session's directory: it holds this project and, beside it, the reports."""
        return self._root.parent

    @classmethod
    def for_run(cls, thread_id: str, env: dict[str, str] | None = None) -> LocalWorkspace:
        """A session's project: `<workspaces_root()>/<thread_id>/project`."""
        return cls(workspaces_root() / thread_id / PROJECT_DIRNAME, env=env)

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
        # The ceiling (set last, so no caller lifts it) keeps git inside the project: its own
        # repository is found first, and nothing above the session's directory ever is.
        environment = {**os.environ, **self._env, **(env or {}), GIT_CEILING_ENV: str(self._root.parent)}
        started = time.monotonic()
        # `bashProvider.ts`: the session environment script (the project's venv activation, upstream's
        # `CLAUDE_ENV_FILE`) runs after the login profile and before the command (Phase 13).
        script = build_command(cmd, environment)

        try:
            completed = subprocess.run(  # noqa: PLW1510 - a failing command is a result, not an error
                ["/bin/bash", "-l", "-c", script],
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
