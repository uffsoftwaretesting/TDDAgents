"""
Resolves an agent's declared execution target (`sandbox` | `local` | `both`) to a
concrete Workspace.

**Forward dependency, recorded deliberately.** The mechanism lands in Phase 1A but the
value it switches on comes from `AgentDefinition.workspace`, which does not exist until
Phase 2. Until then every resolution returns the sandbox, which is exactly today's
behavior — Phase 1 changes no execution target on its own.

`RunTests` never consults this router: it is pinned to the sandbox at the tool level,
because what it measures is the research result.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.workspace.base import (
    CommandResult,
    FileEntry,
    Workspace,
    WorkspaceError,
    WorkspaceKind,
)

logger = logging.getLogger("TDDOrchestrator.WorkspaceRouter")

WorkspaceSpec = Literal["sandbox", "local", "both"]
VALID_SPECS = ("sandbox", "local", "both")

DEFAULT_SPEC: WorkspaceSpec = "sandbox"


class DualWorkspace:
    """
    The `both` target: one Workspace facade over the sandbox and the host.

    Semantics follow from the locked conflict rule ("the sandbox wins for the duration
    of a run"): reads and execution go to the sandbox, writes go to both sides with the
    sandbox first. Nothing can reach this class until AgentDefinition exists in Phase 2;
    re-confirm the semantics there, when they first have a caller.
    """

    # Reads and execution resolve to the sandbox, so results are labelled honestly.
    kind: WorkspaceKind = "sandbox"

    def __init__(self, sandbox: Workspace, local: Workspace) -> None:
        self._sandbox = sandbox
        self._local = local

    @property
    def sandbox(self) -> Workspace:
        return self._sandbox

    @property
    def local(self) -> Workspace:
        return self._local

    def read_file(self, path: str) -> str:
        return self._sandbox.read_file(path)

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        return self._sandbox.list_files(path, depth=depth)

    def exists(self, path: str) -> bool:
        return self._sandbox.exists(path)

    def execute(
        self,
        cmd: str,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        return self._sandbox.execute(cmd, timeout=timeout, env=env)

    def write_file(self, path: str, content: str) -> None:
        self._sandbox.write_file(path, content)
        self._local.write_file(path, content)

    def delete_file(self, path: str) -> None:
        self._sandbox.delete_file(path)
        try:
            self._local.delete_file(path)
        except WorkspaceError as exc:
            # The sandbox is authoritative; a missing local copy is not a failure.
            logger.debug("Local delete of '%s' skipped: %s", path, exc)

    def move(self, old: str, new: str) -> None:
        self._sandbox.move(old, new)
        try:
            self._local.move(old, new)
        except WorkspaceError as exc:
            logger.debug("Local move of '%s' skipped: %s", old, exc)


def resolve_workspace(
    spec: str | None,
    sandbox: Workspace,
    local: Workspace | None = None,
) -> Workspace:
    """
    Maps a declared target onto a Workspace instance.

    Args:
        spec: The agent's `workspace` field. None resolves to the default (sandbox),
              which is every caller in Phase 1A.
        sandbox: The run's E2BWorkspace.
        local: The run's LocalWorkspace. Required for "local" and "both".

    Raises:
        ValueError: The spec is not one of sandbox | local | both, or a local
                    workspace was needed and none was supplied.
    """
    target = (spec or DEFAULT_SPEC).strip().lower()

    if target not in VALID_SPECS:
        raise ValueError(
            f"Unknown workspace target '{spec}'. Expected one of {', '.join(VALID_SPECS)}."
        )

    if target == "sandbox":
        return sandbox

    if local is None:
        raise ValueError(
            f"Workspace target '{target}' requires a local workspace, but none was supplied."
        )

    if target == "local":
        return local

    return DualWorkspace(sandbox, local)
