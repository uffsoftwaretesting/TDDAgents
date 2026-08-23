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

    def __init__(self, kind: str = "sandbox", files: dict[str, str] | None = None) -> None:
        self.kind = kind
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


@pytest.fixture
def make_fake_workspace():
    """
    Factory for tests that need to control both sides — notably the mid-flush abort
    case, where the failure has to be injected on the local side and a real
    LocalWorkspace has no way to fail a write on command.
    """

    def _make(kind: str = "sandbox", files: dict[str, str] | None = None) -> FakeWorkspace:
        return FakeWorkspace(kind=kind, files=files)

    return _make


@pytest.fixture
def local_ws(tmp_path) -> LocalWorkspace:
    return LocalWorkspace(tmp_path / "workspace")


@pytest.fixture
def baseline_path(tmp_path) -> Path:
    return tmp_path / "baseline.json"


@pytest.fixture(autouse=True)
def clear_sync_events():
    """Keeps the module-level event sink from leaking between tests."""
    from app.sync import events

    events.drain()
    yield
    events.drain()
