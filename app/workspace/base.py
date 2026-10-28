"""
The Workspace protocol: one signature per operation, one result type, one exception
family, and identical path semantics on every implementation.

Phase 1A ships two implementations — E2BWorkspace (the remote sandbox) and
LocalWorkspace (the developer's host). Agents and tools are written against this
protocol and never against a provider SDK, so the execution target becomes a
configuration value rather than a code path.

Path semantics, identical on both sides:
    * paths are workspace-relative and use POSIX separators
    * "." denotes the workspace root
    * an absolute path is a real path, as upstream's `expandPath` reads it: one inside the
      workspace root names that file, one outside it (or on a workspace with no host root)
      raises WorkspacePathError, because the workspace is bounded. Until 2026-10-28 a
      leading "/" meant "from the workspace root", which nested a model's correct absolute
      path inside the project while the permission gate approved the real one.
    * escaping the root via ".." raises WorkspacePathError
    * text is UTF-8 everywhere
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

WorkspaceKind = Literal["sandbox", "local"]


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CommandResult:
    """
    The outcome of executing a command in a workspace.

    A non-zero `exit_code` is a result, not an error: in TDD a failing pytest run is
    the expected Red observation, and an agent-authored command that exits non-zero is
    something the agent has to reason about. Only infrastructure failures raise.

    `workspace` labels which side produced the output, so execution results from either
    environment can travel back to an agent through a single channel without losing
    track of where they came from.
    """

    stdout: str
    stderr: str
    exit_code: int
    duration: float
    workspace: WorkspaceKind

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class FileEntry:
    """A single entry returned by `Workspace.list_files`."""

    path: str  # workspace-relative, POSIX separators
    is_dir: bool
    size: int


# ─────────────────────────────────────────────────────────────────────────────
# Exception family
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceError(Exception):
    """
    Base class for every workspace failure.

    Provider exceptions (E2B today, something else tomorrow) are translated into this
    family at the adapter boundary, so no module above `app/sandbox/adapter.py` ever
    sees a provider type.
    """


class WorkspacePathError(WorkspaceError):
    """A path is malformed or escapes the workspace root. A bug, never a blip."""


class WorkspaceNotFound(WorkspaceError):
    """A file, directory, or sandbox does not exist."""


class WorkspaceTimeout(WorkspaceError):
    """An operation or command exceeded its allotted time."""


class WorkspaceRateLimited(WorkspaceError):
    """The execution provider rate-limited the request."""


class WorkspaceAuthError(WorkspaceError):
    """Authentication against the execution provider failed."""


class WorkspaceCapacityError(WorkspaceError):
    """The workspace ran out of disk space."""


class WorkspaceTemplateError(WorkspaceError):
    """The sandbox template is invalid or unavailable."""


class WorkspaceTransportError(WorkspaceError):
    """A recognized but unspecific failure while talking to the execution environment."""


class WorkspaceProviderError(WorkspaceError):
    """An unclassified failure. Fail closed: treated as fatal."""


# ─────────────────────────────────────────────────────────────────────────────
# Path normalization — shared by every implementation
# ─────────────────────────────────────────────────────────────────────────────

def normalize_path(path: str, root: str | None = None) -> str:
    """
    Normalizes a caller-supplied path to the canonical workspace-relative form. `root` is
    the workspace's absolute root, which an absolute `path` must lie under.

    This is the single place the path contract is enforced, so E2BWorkspace and
    LocalWorkspace cannot drift apart on it.

    Returns:
        A POSIX-separated relative path, or "." for the workspace root.

    Raises:
        WorkspacePathError: The path is empty, escapes the workspace root, or is absolute
            and not under `root`.
    """
    if path is None:
        raise WorkspacePathError("Path must not be None.")

    candidate = str(path).strip().replace("\\", "/")
    if not candidate:
        raise WorkspacePathError("Path must not be empty.")

    if candidate.startswith("/"):
        absolute = posixpath.normpath("/" + candidate.lstrip("/"))
        given = str(root).replace("\\", "/") if root is not None else ""
        if not given.startswith("/"):
            raise WorkspacePathError(f"Path '{path}' is absolute, and this workspace has no host root.")
        base = posixpath.normpath("/" + given.lstrip("/"))
        if absolute == base:
            return "."
        if not absolute.startswith(base.rstrip("/") + "/"):
            raise WorkspacePathError(f"Path '{path}' is outside the project ({base}).")
        candidate = absolute[len(base.rstrip("/")) + 1:]

    normalized = posixpath.normpath(candidate)

    if normalized == ".":
        return "."
    if normalized == ".." or normalized.startswith("../"):
        raise WorkspacePathError(
            f"Path '{path}' escapes the workspace root."
        )

    return normalized


def workspace_path(ws: object, path: str) -> str:
    """`normalize_path` against `ws`'s own root (its `root` attribute, if it has one)."""
    root = getattr(ws, "root", None)
    return normalize_path(path, None if root is None else str(root))


# ─────────────────────────────────────────────────────────────────────────────
# The protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class Workspace(Protocol):
    """
    The compatibility guarantee expressed as a type: any two implementations must be
    interchangeable for every operation below, including path semantics, encoding, and
    the exceptions raised on failure.
    """

    kind: WorkspaceKind

    def read_file(self, path: str) -> str:
        """Reads a UTF-8 text file. Raises WorkspaceNotFound if it does not exist."""
        ...

    def write_file(self, path: str, content: str) -> None:
        """Writes UTF-8 text, creating parent directories and overwriting any existing file."""
        ...

    def delete_file(self, path: str) -> None:
        """Removes a file or directory. Raises WorkspaceNotFound if it does not exist."""
        ...

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        """Lists entries under `path`, descending `depth` levels."""
        ...

    def exists(self, path: str) -> bool:
        """Reports whether a file or directory exists."""
        ...

    def move(self, old: str, new: str) -> None:
        """Renames or moves a file or directory."""
        ...

    def execute(
        self,
        cmd: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """
        Runs a shell command from the workspace root.

        A non-zero exit code is returned in the CommandResult, not raised. Only
        infrastructure failures raise a WorkspaceError.
        """
        ...
