"""
LocalWorkspace — the Workspace protocol over the developer's host filesystem.

Full parity with E2BWorkspace, including `execute()`. This is deliberate and the
trade-off was accepted when the two-execution-target design was locked: there is no
per-call override and no command allowlist, so the only thing standing between an
agent and an arbitrary host command is the `workspace:` value in its definition file.
Since Phase 13 every session's project has its own folder outside TDDAgents' own:
`<workspaces root>/<thread>/project` (`app/workspace/project.py`), as claude-code works in the
folder it is opened in. There is no pre-built environment (2026-11-03): the project sets itself up
from its own manifests, and TDDAgents' own interpreter is kept off its `PATH`
(`orchestrator_free_environment`). Reports go beside it, in `<thread>/metrics_and_logging/`, outside the git tree.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
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


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, ValueError):
        return False


def orchestrator_free_environment(environ: Mapping[str, str], prefix: str = sys.prefix) -> dict[str, str]:
    """
    `environ` without TDDAgents' own interpreter (2026-11-03): its `<prefix>/bin` directory removed
    from `PATH`, and `VIRTUAL_ENV` dropped when it names that prefix. TDDAgents runs from its own
    virtualenv; without this a project's `python` or `pip` would resolve to the orchestrator's, and a
    package installed there would make the project's tests pass for a reason the project never declared.
    """
    result = dict(environ)
    own_bin = os.path.join(prefix, "bin")
    if "PATH" in result:
        result["PATH"] = os.pathsep.join(entry for entry in result["PATH"].split(os.pathsep)
                                         if not entry or not _same_path(entry, own_bin))
    if result.get("VIRTUAL_ENV") and _same_path(result["VIRTUAL_ENV"], prefix):
        del result["VIRTUAL_ENV"]
    return result


def kill_process_group(pid: int) -> None:
    """`treeKill(pid, 'SIGKILL')`: the shell leads its own process group, so the group is the tree."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class LocalShellProcess:
    """A host shell command spawned by `LocalWorkspace.spawn`."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def pid(self) -> int | None:
        return self._process.pid

    async def wait(self) -> int:
        return await self._process.wait()

    def kill_tree(self) -> None:
        kill_process_group(self._process.pid)


class LocalWorkspace:
    """A Workspace rooted at a single directory on the host."""

    kind: WorkspaceKind = "local"

    def __init__(
        self, root: str | os.PathLike[str], env: dict[str, str] | None = None,
        run_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # Merged into every command, under the per-call `env`: e.g. the session venv's PATH.
        self._env: dict[str, str] = dict(env or {})
        self._run_dir = Path(run_dir) if run_dir is not None else None

    @property
    def run_dir(self) -> Path:
        """
        The session's directory: it holds this project and, beside it, the reports. A forked
        session's project is its source's (`--fork-session`), so its directory is given, not derived.
        """
        return self._run_dir if self._run_dir is not None else self._root.parent

    @classmethod
    def for_run(cls, thread_id: str, env: dict[str, str] | None = None) -> LocalWorkspace:
        """A session's project: `<workspaces_root()>/<thread_id>/project` (a link, for a fork)."""
        run_dir = workspaces_root() / thread_id
        return cls(run_dir / PROJECT_DIRNAME, env=env, run_dir=run_dir)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str) -> Path:
        """
        Turns a workspace-relative path into an absolute host path.

        `normalize_path` already rejects `..` escapes; the containment check below is
        the second line of defense, catching an escape smuggled in through a symlink.
        """
        relative = normalize_path(path, str(self._root))
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

    def walk_files(self, prune: Callable[[str], bool]) -> list[str]:
        """
        Every file under the root, as root-relative POSIX paths, without descending into a directory
        `prune` refuses (it is given the directory's relative path with a trailing `/`). The TDD walks
        (`app/tdd/suite_fingerprint.py::project_files`) prune the declared `exclude` patterns, so a
        dependency tree such as `node_modules/` is never listed, let alone read. Symlinked
        directories are not followed.
        """
        files: list[str] = []
        for current, dirnames, filenames in os.walk(self._root):
            relative = Path(current).relative_to(self._root).as_posix()
            prefix = "" if relative == "." else f"{relative}/"
            dirnames[:] = sorted(name for name in dirnames if not prune(f"{prefix}{name}/"))
            files.extend(f"{prefix}{name}" for name in filenames)
        return sorted(files)

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

    def _environment(self, env: dict[str, str] | None = None) -> dict[str, str]:
        # The project never resolves TDDAgents' own interpreter (2026-11-03): its `bin` leaves `PATH`
        # and its `VIRTUAL_ENV` goes. The ceiling (set last, so no caller lifts it) keeps git inside
        # the project: its own repository is found first, and nothing above the session's directory ever is.
        base = orchestrator_free_environment(os.environ)
        return {**base, **self._env, **(env or {}), GIT_CEILING_ENV: str(self._root.parent)}

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

        stdout and stderr go to files and the call waits for the shell's *exit*
        (`ShellCommand.ts`: "'close' waits for stdio to close, which includes grandchild
        processes that inherit file descriptors (e.g. `sleep 30 &`)"), so a command that leaves
        something running in the background returns as soon as the shell is done. On timeout
        the shell's whole process group is killed (upstream's `treeKill`).
        """
        limit = timeout if timeout is not None else Config.COMMAND_TIMEOUT
        environment = self._environment(env)
        started = time.monotonic()
        # `bashProvider.ts`: the session environment script (the project's venv activation, upstream's
        # `CLAUDE_ENV_FILE`) runs after the login profile and before the command (Phase 13).
        script = build_command(cmd, environment)

        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                process = subprocess.Popen(
                    ["/bin/bash", "-l", "-c", script],
                    cwd=str(self._root),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
            except OSError as exc:
                raise WorkspaceProviderError(f"Could not run local command '{cmd}': {exc}") from exc
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired as exc:
                kill_process_group(process.pid)
                process.wait()
                raise WorkspaceTimeout(f"Local command exceeded {limit}s: {cmd}") from exc
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", errors="replace")
            stderr = err.read().decode("utf-8", errors="replace")

        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=code,
            duration=time.monotonic() - started,
            workspace="local",
        )

    async def spawn(
        self, cmd: str, *, output_fd: int, cwd: str | None = None, cwd_file: str | None = None
    ) -> LocalShellProcess:
        """
        `Shell.ts` -> `exec` on the host: `bash -l -c` with the session environment, output to
        `output_fd`, stdin on `/dev/null`, its own process group (upstream's `detached`).
        """
        environment = self._environment()
        script = build_command(cmd, environment, cwd_file)
        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash", "-l", "-c", script,
                cwd=cwd or str(self._root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output_fd,
                stderr=output_fd,
                start_new_session=True,
            )
        except OSError as exc:
            raise WorkspaceProviderError(f"Could not run local command '{cmd}': {exc}") from exc
        return LocalShellProcess(process)
