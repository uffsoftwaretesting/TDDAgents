"""
E2BAdapter — the ONLY module in this codebase that imports the E2B SDK.

Everything above this file speaks the vocabulary of `app/workspace/base.py`: relative
POSIX paths, CommandResult, and the WorkspaceError family. Swapping the sandbox
provider means rewriting this file and nothing else.

**Exempt from the CLAUDE.md quality gate's unit- and mutation-testing requirement.**
This module *is* the boundary to the E2B SDK, so there is nothing left to fake beneath
it; testing it offline would only assert that mocks call mocks. In place of that it
requires a static surface check — every SDK method and kwarg used below must still exist
on the installed `e2b` package — plus an end-to-end pipeline run. Everything layered
above it, `app/workspace/e2b.py` included, is fully testable and is *not* exempt.

Three lifecycle defects are fixed here rather than at the call sites:

1. `Sandbox.create()` was called with no `timeout`, taking the SDK default of 300s
   while real runs last far longer. The adapter creates with Config.SANDBOX_TIMEOUT
   and slides the window forward from inside its own operations.
2. `commands.run()` defaults to `timeout=60` and no call site overrode it, so a long
   pip install or test suite could be truncated. The adapter defaults to
   Config.COMMAND_TIMEOUT and accepts a per-call override.
3. Resuming a run reconnected to a sandbox that may no longer exist. `reuse_or_create`
   probes first and provisions a fresh one when the old one is gone.
"""

from __future__ import annotations

import logging
import posixpath
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List

from e2b_code_interpreter import Sandbox
from e2b import (
    AuthenticationException,
    NotEnoughSpaceException,
    NotFoundException,
    SandboxException,
    TemplateException,
    TimeoutException,
)
from e2b.exceptions import RateLimitException

from app.config.config import Config
from app.workspace.base import (
    CommandResult,
    FileEntry,
    WorkspaceAuthError,
    WorkspaceCapacityError,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspaceProviderError,
    WorkspaceRateLimited,
    WorkspaceTemplateError,
    WorkspaceTimeout,
    WorkspaceTransportError,
    normalize_path,
)

logger = logging.getLogger("TDDOrchestrator.Sandbox")

# Commands run as root, matching the behavior every recorded experimental run relied on.
_SANDBOX_USER = "root"


@dataclass
class BackgroundCommand:
    """
    A command started with `background=True`, plus the output accumulated so far.

    The SDK streams output through callbacks that run on its own thread, so every buffer
    access is locked. `drain()` returns what has arrived since the last call, which is the
    shape `BashOutput` needs: an agent polls repeatedly and should not re-read what it has
    already seen.
    """

    id: str
    cmd: str
    handle: Any = None
    finished: bool = False
    exit_code: int | None = None
    _stdout: list[str] = field(default_factory=list)
    _stderr: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def pid(self) -> int | None:
        return getattr(self.handle, "pid", None)

    def append_stdout(self, chunk: str) -> None:
        with self._lock:
            self._stdout.append(chunk)

    def append_stderr(self, chunk: str) -> None:
        with self._lock:
            self._stderr.append(chunk)

    def drain(self) -> tuple[str, str]:
        """Returns (stdout, stderr) received since the previous drain, and clears both."""
        with self._lock:
            out, err = "".join(self._stdout), "".join(self._stderr)
            self._stdout.clear()
            self._stderr.clear()
        return out, err

    def peek(self) -> tuple[str, str]:
        """Same as `drain` but leaves the buffers in place."""
        with self._lock:
            return "".join(self._stdout), "".join(self._stderr)


def _translate(exc: Exception, operation: str) -> WorkspaceError:
    """
    Maps an E2B SDK exception onto the WorkspaceError family.

    The mapping is one-for-one with the classification the pipeline used before this
    refactor, so retry behavior is unchanged; only the exception type crossing the
    boundary is different.
    """
    prefix = f"[{operation}] "

    if isinstance(exc, TimeoutException):
        return WorkspaceTimeout(f"{prefix}E2B timeout reached.")
    if isinstance(exc, RateLimitException):
        return WorkspaceRateLimited(f"{prefix}E2B rate limit reached.")
    if isinstance(exc, AuthenticationException):
        return WorkspaceAuthError(f"{prefix}E2B authentication failed.")
    if isinstance(exc, NotFoundException):
        return WorkspaceNotFound(f"{prefix}Sandbox or resource not found in E2B: {exc}")
    if isinstance(exc, NotEnoughSpaceException):
        return WorkspaceCapacityError(f"{prefix}Insufficient disk space in the sandbox.")
    if isinstance(exc, TemplateException):
        return WorkspaceTemplateError(f"{prefix}Invalid sandbox template.")

    # SandboxException is the base class of everything above, so it must come last.
    if isinstance(exc, SandboxException):
        return WorkspaceTransportError(f"{prefix}Sandbox error: {exc}")

    return WorkspaceProviderError(f"{prefix}Unclassified infrastructure failure: {exc}")


class E2BAdapter:
    """
    A thin, stateful wrapper around one E2B sandbox instance.

    Construct it with `create`, `connect`, or `reuse_or_create`; never with the
    constructor directly from outside this module.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox
        self._last_refresh = time.monotonic()
        self._background: dict[str, BackgroundCommand] = {}

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def create(cls, timeout: int | None = None) -> "E2BAdapter":
        """Provisions a new sandbox with an explicit lifetime."""
        lifetime = timeout if timeout is not None else Config.SANDBOX_TIMEOUT
        try:
            sandbox = Sandbox.create(api_key=Config.E2B_API_KEY, timeout=lifetime)
        except Exception as exc:
            raise _translate(exc, "Sandbox.create") from exc

        logger.info("☁️  Sandbox %s created with a %ss lifetime.", sandbox.sandbox_id, lifetime)
        return cls(sandbox)

    @classmethod
    def connect(cls, sandbox_id: str) -> "E2BAdapter":
        """Connects to an existing sandbox. Raises WorkspaceNotFound if it is gone."""
        try:
            sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)
        except Exception as exc:
            raise _translate(exc, "Sandbox.connect") from exc

        return cls(sandbox)

    @classmethod
    def reuse_or_create(cls, sandbox_id: str | None) -> "E2BAdapter":
        """
        Returns an adapter for `sandbox_id` when that sandbox is still alive, and a
        freshly provisioned one otherwise.

        Resuming a run used to hand a stale id straight to `connect`. Probing first
        means a resume degrades to "start with an empty sandbox" instead of failing
        outright. Rehydrating that fresh sandbox from the ledger is the sync engine's
        job and is not wired up yet, so the caller is told what happened.
        """
        if not sandbox_id:
            return cls.create()

        try:
            adapter = cls.connect(sandbox_id)
            if adapter.is_running():
                logger.info("♻️  Reusing live sandbox %s.", sandbox_id)
                return adapter
            logger.warning("⚠️ Sandbox %s exists but is not running. Provisioning a new one.", sandbox_id)
        except WorkspaceError as exc:
            logger.warning("⚠️ Could not reuse sandbox %s (%s). Provisioning a new one.", sandbox_id, exc)

        adapter = cls.create()
        logger.warning(
            "⚠️ The new sandbox %s is empty. Files from the previous run are not "
            "restored — sandbox rehydration lands with the sync engine.",
            adapter.sandbox_id,
        )
        return adapter

    # ── Identity and lifecycle ───────────────────────────────────────────────

    @property
    def sandbox_id(self) -> str:
        # The E2B SDK ships no py.typed marker, so everything it returns is Any.
        # Casting at this boundary is what keeps the rest of the codebase typed.
        return str(self._sandbox.sandbox_id)

    def is_running(self) -> bool:
        try:
            return bool(self._sandbox.is_running())
        except Exception as exc:
            raise _translate(exc, "is_running") from exc

    def info(self) -> Any:
        try:
            return self._sandbox.get_info()
        except Exception as exc:
            raise _translate(exc, "info") from exc

    def refresh_timeout(self, force: bool = False) -> bool:
        """
        Slides the sandbox lifetime forward, at most once per
        Config.SANDBOX_REFRESH_INTERVAL unless `force` is set.

        Called from the operations below rather than from a timer: a background thread
        would make a run's sandbox lifetime depend on wall-clock scheduling, and the
        three-runs-per-task research design needs every run replayable from its own
        call sequence. Sync checkpoints will call this with force=True once they exist.

        Returns:
            True if the timeout was actually pushed out to the provider.
        """
        now = time.monotonic()
        if not force and (now - self._last_refresh) < Config.SANDBOX_REFRESH_INTERVAL:
            return False

        try:
            self._sandbox.set_timeout(Config.SANDBOX_TIMEOUT)
        except Exception as exc:
            raise _translate(exc, "refresh_timeout") from exc

        self._last_refresh = now
        logger.info("⏱️  Sandbox %s lifetime extended to %ss.", self.sandbox_id, Config.SANDBOX_TIMEOUT)
        return True

    def kill(self) -> None:
        try:
            self._sandbox.kill()
        except Exception as exc:
            raise _translate(exc, "kill") from exc

    # ── Paths ────────────────────────────────────────────────────────────────

    def resolve(self, path: str) -> str:
        """Turns a workspace-relative path into an absolute sandbox path."""
        relative = normalize_path(path)
        if relative == ".":
            return Config.SANDBOX_WORKSPACE_ROOT
        return posixpath.join(Config.SANDBOX_WORKSPACE_ROOT, relative)

    def _to_relative(self, absolute: str) -> str:
        """Inverse of `resolve`, for values coming back out of the SDK."""
        root = Config.SANDBOX_WORKSPACE_ROOT.rstrip("/")
        if absolute == root:
            return "."
        if absolute.startswith(root + "/"):
            return absolute[len(root) + 1:]
        return absolute.lstrip("/")

    # ── Files ────────────────────────────────────────────────────────────────

    def read(self, path: str) -> str:
        target = self.resolve(path)
        try:
            return str(self._sandbox.files.read(target))
        except Exception as exc:
            raise _translate(exc, f"read {path}") from exc

    def write(self, path: str, content: str) -> None:
        self.refresh_timeout()
        target = self.resolve(path)
        try:
            # Single-file write creates missing parent directories on its own.
            self._sandbox.files.write(target, content)
        except Exception as exc:
            raise _translate(exc, f"write {path}") from exc

    def write_many(self, files: dict[str, str]) -> None:
        """
        Batch write. Unlike the single-file call, `write_files` does NOT create missing
        parent directories, so they are created first.
        """
        if not files:
            return

        self.refresh_timeout()
        entries = [{"path": self.resolve(p), "data": c} for p, c in files.items()]
        parents = {posixpath.dirname(e["path"]) for e in entries}

        try:
            for parent in sorted(parents):
                if parent:
                    self._sandbox.files.make_dir(parent)
            self._sandbox.files.write_files(entries)
        except Exception as exc:
            raise _translate(exc, "write_many") from exc

    def list(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        target = self.resolve(path)
        try:
            entries = self._sandbox.files.list(target, depth=depth)
        except Exception as exc:
            raise _translate(exc, f"list {path}") from exc

        return [
            FileEntry(
                path=self._to_relative(entry.path),
                is_dir=(getattr(entry.type, "value", entry.type) == "dir"),
                size=getattr(entry, "size", 0) or 0,
            )
            for entry in entries
        ]

    def exists(self, path: str) -> bool:
        target = self.resolve(path)
        try:
            return bool(self._sandbox.files.exists(target))
        except NotFoundException:
            return False
        except Exception as exc:
            raise _translate(exc, f"exists {path}") from exc

    def remove(self, path: str) -> None:
        self.refresh_timeout()
        target = self.resolve(path)
        try:
            self._sandbox.files.remove(target)
        except Exception as exc:
            raise _translate(exc, f"remove {path}") from exc

    def rename(self, old: str, new: str) -> None:
        self.refresh_timeout()
        source, destination = self.resolve(old), self.resolve(new)
        try:
            parent = posixpath.dirname(destination)
            if parent:
                self._sandbox.files.make_dir(parent)
            self._sandbox.files.rename(source, destination)
        except Exception as exc:
            raise _translate(exc, f"rename {old} -> {new}") from exc

    def make_dir(self, path: str) -> None:
        self.refresh_timeout()
        target = self.resolve(path)
        try:
            self._sandbox.files.make_dir(target)
        except Exception as exc:
            raise _translate(exc, f"make_dir {path}") from exc

    # ── Execution ────────────────────────────────────────────────────────────

    def execute(
        self,
        cmd: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> CommandResult:
        """
        Runs a shell command from the sandbox workspace root.

        A non-zero exit code comes back inside the CommandResult rather than as an
        exception. E2B signals it with CommandExitException, which subclasses both
        SandboxException and CommandResult; catching it here is what lets a failing
        pytest run (the Red observation) and a bad agent-authored command be treated
        as data instead of as infrastructure failure.
        """
        self.refresh_timeout()

        working_dir = self.resolve(cwd) if cwd else Config.SANDBOX_WORKSPACE_ROOT
        limit = timeout if timeout is not None else Config.COMMAND_TIMEOUT
        started = time.monotonic()

        try:
            result = self._sandbox.commands.run(
                cmd,
                user=_SANDBOX_USER,
                cwd=working_dir,
                envs=env,
                timeout=limit,
            )
            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_code,
                duration=time.monotonic() - started,
                workspace="sandbox",
            )

        except SandboxException as exc:
            # A non-zero exit is a result, not a failure.
            if type(exc).__name__ == "CommandExitException":
                return CommandResult(
                    stdout=getattr(exc, "stdout", "") or "",
                    stderr=getattr(exc, "stderr", "") or "",
                    exit_code=getattr(exc, "exit_code", 1),
                    duration=time.monotonic() - started,
                    workspace="sandbox",
                )
            raise _translate(exc, "execute") from exc

        except Exception as exc:
            raise _translate(exc, "execute") from exc

    # ── Background commands ──────────────────────────────────────────────────

    def start_background(self, cmd: str, cwd: str | None = None) -> "BackgroundCommand":
        """
        Starts a command without waiting for it, and returns a handle to poll.

        The SDK has no "read whatever has accumulated so far" call, so output is captured
        through the `on_stdout` / `on_stderr` callbacks into a buffer the handle owns.
        Those callbacks fire on the SDK's own thread, which is why BackgroundCommand locks
        around its buffers.

        Sandbox-only by design: `BashOutput` and `KillShell` are pinned to the sandbox at
        the tool level, so there is no local counterpart to keep in step.
        """
        self.refresh_timeout()
        working_dir = self.resolve(cwd) if cwd else Config.SANDBOX_WORKSPACE_ROOT
        command = BackgroundCommand(id=f"bash_{uuid.uuid4().hex[:8]}", cmd=cmd)

        try:
            command.handle = self._sandbox.commands.run(
                cmd,
                background=True,
                user=_SANDBOX_USER,
                cwd=working_dir,
                on_stdout=command.append_stdout,
                on_stderr=command.append_stderr,
            )
        except Exception as exc:
            raise _translate(exc, f"start_background {cmd}") from exc

        self._background[command.id] = command
        logger.info("▶️  Background command %s started (pid %s).", command.id, command.pid)
        return command

    def get_background(self, command_id: str) -> "BackgroundCommand | None":
        return self._background.get(command_id)

    def list_background(self) -> List["BackgroundCommand"]:
        return list(self._background.values())

    def kill_command(self, command_id: str) -> bool:
        """
        Kills one background command. Returns False when the id is unknown.

        A command that has already exited is not an error to kill — the caller usually
        cannot know which happened, and treating it as one would make teardown noisy.
        """
        command = self._background.get(command_id)
        if command is None:
            return False

        try:
            if command.handle is not None:
                command.handle.kill()
        except Exception as exc:
            logger.debug("Killing %s reported: %s", command_id, exc)

        command.finished = True
        self._background.pop(command_id, None)
        return True

    def kill_all_background(self) -> int:
        """Teardown helper: kills every background command this adapter started."""
        killed = 0
        for command_id in list(self._background):
            if self.kill_command(command_id):
                killed += 1
        return killed

    def run_code(self, code: str, language: str | None = None) -> Any:
        """
        Runs code in the sandbox's stateful Jupyter-style REPL.

        This is the one capability `e2b_code_interpreter` adds over plain `e2b`, and
        the reason the codebase standardizes on it. Surfaced as the RunCode tool in
        Phase 1B; the raw Execution object is returned for that tool to shape.
        """
        self.refresh_timeout()
        try:
            return self._sandbox.run_code(code, language=language)
        except Exception as exc:
            raise _translate(exc, "run_code") from exc
