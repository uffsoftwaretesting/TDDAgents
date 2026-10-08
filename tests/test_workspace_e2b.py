"""
E2BWorkspace against a fake adapter.

This is the "test behind the seam" rule applied to the module that sits directly on the
seam. E2BWorkspace itself contains no logic — it exists so that everything above it
speaks the Workspace protocol instead of speaking E2B. What is worth asserting is
therefore exactly that: each method reaches the right adapter call, with the arguments
intact and nothing quietly reinterpreted on the way through.

app/sandbox/adapter.py, one layer below, is the module that genuinely cannot be unit
tested offline and is the only exempt module in the roster.
"""

from __future__ import annotations

import pytest

from app.workspace.base import CommandResult, FileEntry, Workspace
from app.workspace.e2b import E2BWorkspace


class FakeAdapter:
    """Records every call so a test can assert on delegation."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.sandbox_id = "sbx-fake-123"

    def read(self, path):
        self.calls.append(("read", path))
        return "file contents"

    def write(self, path, content):
        self.calls.append(("write", path, content))

    def remove(self, path):
        self.calls.append(("remove", path))

    def list(self, path, depth=1):
        self.calls.append(("list", path, depth))
        return [FileEntry(path="src/main.py", is_dir=False, size=12)]

    def exists(self, path):
        self.calls.append(("exists", path))
        return True

    def rename(self, old, new):
        self.calls.append(("rename", old, new))

    def execute(self, cmd, timeout=None, env=None):
        self.calls.append(("execute", cmd, timeout, env))
        return CommandResult(
            stdout="out", stderr="", exit_code=0, duration=0.1, workspace="sandbox"
        )


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def ws(adapter) -> E2BWorkspace:
    return E2BWorkspace(adapter)


def test_satisfies_the_workspace_protocol(ws):
    assert isinstance(ws, Workspace)
    assert ws.kind == "sandbox"


def test_exposes_its_adapter_and_sandbox_id(ws, adapter):
    # SyncEngine reaches for `.adapter` to refresh the sandbox lifetime at a checkpoint.
    assert ws.adapter is adapter
    assert ws.sandbox_id == "sbx-fake-123"


def test_read_file_delegates(ws, adapter):
    assert ws.read_file("src/main.py") == "file contents"
    assert adapter.calls == [("read", "src/main.py")]


def test_write_file_delegates(ws, adapter):
    ws.write_file("src/main.py", "print(1)")
    assert adapter.calls == [("write", "src/main.py", "print(1)")]


def test_delete_file_delegates_to_remove(ws, adapter):
    ws.delete_file("gone.py")
    assert adapter.calls == [("remove", "gone.py")]


def test_list_files_delegates_and_passes_depth(ws, adapter):
    entries = ws.list_files("src", depth=3)
    assert adapter.calls == [("list", "src", 3)]
    assert entries[0].path == "src/main.py"


def test_list_files_defaults_to_root_and_depth_one(ws, adapter):
    ws.list_files()
    assert adapter.calls == [("list", ".", 1)]


def test_exists_delegates(ws, adapter):
    assert ws.exists("src/main.py") is True
    assert adapter.calls == [("exists", "src/main.py")]


def test_move_delegates_to_rename(ws, adapter):
    ws.move("old.py", "new.py")
    assert adapter.calls == [("rename", "old.py", "new.py")]


def test_execute_delegates_and_forwards_timeout_and_env(ws, adapter):
    result = ws.execute("pytest", timeout=600, env={"K": "V"})
    assert adapter.calls == [("execute", "pytest", 600, {"K": "V"})]
    assert result.exit_code == 0
    assert result.workspace == "sandbox"


def test_execute_defaults_leave_timeout_and_env_unset(ws, adapter):
    """The adapter, not this layer, owns the default command timeout."""
    ws.execute("ls")
    assert adapter.calls == [("execute", "ls", None, None)]
