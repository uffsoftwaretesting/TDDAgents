"""
Shared fixtures for the Phase 1A substrate tests.

These tests are offline by design: no E2B credentials, no sandbox, no LLM, no Postgres.
They cover LocalWorkspace directly and exercise the sync engine against an in-memory
FakeWorkspace, so the whole suite runs in seconds and can be part of an ordinary edit
loop. The live E2B half of the parity check belongs to the Phase 1B harness.

`app/config/config.py` raises at import time when the three API keys are unset, so the
environment is seeded before any `app.*` import happens.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("E2B_API_KEY", "test-e2b-key")
os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")

# Allows `pytest tests/` from the repo root without an installed package.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from app.workspace.base import (  # noqa: E402
    CommandResult,
    FileEntry,
    WorkspaceKind,
    WorkspaceNotFound,
    normalize_path,
)
from app.workspace.local import LocalWorkspace  # noqa: E402


class FakeWorkspace:
    """
    An in-memory Workspace, used as the sandbox side in sync tests.

    It implements the protocol faithfully enough that the engine cannot tell the
    difference, and it lets a test assert on exactly which operations ran — including
    failing one on demand, which is how the mid-flush abort case is exercised.
    """

    def __init__(
        self, kind: WorkspaceKind = "sandbox", files: dict[str, str] | None = None
    ) -> None:
        self.kind: WorkspaceKind = kind
        self.files: dict[str, str] = dict(files or {})
        self.command_log: list[str] = []
        self.command_results: dict[str, CommandResult] = {}
        self.fail_write_on: set[str] = set()

    # ── Workspace protocol ───────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        key = normalize_path(path)
        if key not in self.files:
            raise WorkspaceNotFound(f"File not found: {path}")
        return self.files[key]

    def write_file(self, path: str, content: str) -> None:
        key = normalize_path(path)
        if key in self.fail_write_on:
            raise RuntimeError(f"Injected write failure for '{key}'")
        self.files[key] = content

    def delete_file(self, path: str) -> None:
        key = normalize_path(path)
        if key not in self.files:
            raise WorkspaceNotFound(f"File not found: {path}")
        del self.files[key]

    def list_files(self, path: str = ".", depth: int = 1) -> list[FileEntry]:
        prefix = "" if normalize_path(path) == "." else normalize_path(path) + "/"
        return [
            FileEntry(path=p, is_dir=False, size=len(c.encode("utf-8")))
            for p, c in sorted(self.files.items())
            if p.startswith(prefix)
        ]

    def exists(self, path: str) -> bool:
        return normalize_path(path) in self.files

    def move(self, old: str, new: str) -> None:
        self.files[normalize_path(new)] = self.read_file(old)
        del self.files[normalize_path(old)]

    def execute(self, cmd: str, timeout: float | None = None, env: dict | None = None) -> CommandResult:
        self.command_log.append(cmd)
        if cmd in self.command_results:
            return self.command_results[cmd]
        return CommandResult(stdout="", stderr="", exit_code=0, duration=0.0, workspace=self.kind)


@pytest.fixture
def fake_sandbox() -> FakeWorkspace:
    return FakeWorkspace(kind="sandbox")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1B — tool-layer fixtures
# ─────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel  # noqa: E402

from app.tools.base import (  # noqa: E402
    Capability,
    ToolContext,
    ToolResult,
    build_tool,
)


class EchoArgs(BaseModel):
    """The stand-in input schema for tool-layer tests that do not care about the schema."""

    text: str = "hello"


class CommandArgs(BaseModel):
    """Carries a `command` field, which is what a hook's `if:` condition matches against."""

    command: str


def make_tool(
    name: str = "Echo",
    *,
    call: Any = None,
    args_schema: type[BaseModel] = EchoArgs,
    **overrides: Any,
):
    """
    A minimal real tool, built through `build_tool` so the fail-closed defaults are
    exercised rather than bypassed.
    """
    def _default_call(args, ctx) -> ToolResult:
        return ToolResult(tool_name=name, content=getattr(args, "text", ""))

    description = overrides.pop("description", None)

    return build_tool(
        name=name,
        args_schema=args_schema,
        prompt=f"The {name} tool.",
        call=call if call is not None else _default_call,
        description=description if description is not None else (lambda args: name),
        **overrides,
    )


def make_read_tool(name: str = "Reader", **overrides: Any):
    """A read-only, concurrency-safe tool — the shape every read tool in the roster has."""
    defaults: dict[str, Any] = {
        "is_read_only": lambda args: True,
        "is_concurrency_safe": lambda args: True,
        "required_capability": lambda args: Capability.READ,
    }
    defaults.update(overrides)
    return make_tool(name, **defaults)


@pytest.fixture
def tool_ctx(fake_sandbox: FakeWorkspace) -> ToolContext:
    """A permissive context, so a test opts into restrictions rather than out of them."""
    return ToolContext(
        workspace=fake_sandbox,
        workspace_spec="sandbox",
        permission_mode="full",
        session_id="test-session",
        agent_id="agent-1",
        agent_type="tester",
    )


@pytest.fixture
def make_tool_ctx(fake_sandbox: FakeWorkspace):
    def _make(**overrides: Any) -> ToolContext:
        fields: dict[str, Any] = {
            "workspace": fake_sandbox,
            "workspace_spec": "sandbox",
            "permission_mode": "full",
            "session_id": "test-session",
        }
        fields.update(overrides)
        return ToolContext(**fields)

    return _make


@pytest.fixture
def make_fake_workspace():
    """
    Factory for tests that need to control both sides — notably the mid-flush abort
    case, where the failure has to be injected on the local side and a real
    LocalWorkspace has no way to fail a write on command.
    """

    def _make(
        kind: WorkspaceKind = "sandbox", files: dict[str, str] | None = None
    ) -> FakeWorkspace:
        return FakeWorkspace(kind=kind, files=files)

    return _make


@pytest.fixture
def local_ws(tmp_path: Path) -> LocalWorkspace:
    return LocalWorkspace(tmp_path / "workspace")


@pytest.fixture
def baseline_path(tmp_path: Path) -> Path:
    return tmp_path / "baseline.json"


@pytest.fixture(autouse=True)
def clear_sync_events():
    """Keeps the module-level event sink from leaking between tests."""
    from app.sync import events

    events.drain()
    yield
    events.drain()


@pytest.fixture
def local_tool_ctx(local_ws: LocalWorkspace) -> ToolContext:
    """
    A context backed by a real LocalWorkspace in tmp_path.

    Grep and Glob shell out to `grep` and `find`, so they need a workspace whose
    `execute` runs an actual process — FakeWorkspace returns canned results and would
    only prove the tools build a command string.
    """
    return ToolContext(
        workspace=local_ws,
        workspace_spec="local",
        permission_mode="full",
        session_id="test-session",
    )
