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


def test_spawn_copies_the_background_output_into_the_file_and_returns_the_exit_code(adapter, tmp_path):
    import asyncio
    import os
    import threading

    from app.sandbox.adapter import BackgroundCommand
    from app.workspace import e2b

    done = threading.Event()

    class Handle:
        pid = 7
        killed = False

        def wait(self):
            done.wait(2)
            if self.killed:
                raise RuntimeError("killed")
            error = RuntimeError("exit")
            error.exit_code = 3  # type: ignore[attr-defined]
            raise error

        def kill(self):
            self.killed = True
            done.set()

    background = BackgroundCommand(id="bash_1", cmd="make", handle=Handle())
    started: list[tuple] = []

    def start_background(cmd, cwd=None):
        started.append((cmd, cwd))
        background.append_stdout("out\n")
        return background

    adapter.start_background = start_background
    adapter.kill_command = lambda command_id: background.handle.kill() or True
    path = tmp_path / "b1.output"

    async def go():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        process = await e2b.E2BWorkspace(adapter).spawn("make", output_fd=fd, cwd="/work", cwd_file="/ignored")
        os.close(fd)
        background.append_stderr("err\n")
        done.set()
        return process.pid, await process.wait()

    assert asyncio.run(go()) == (7, 3)
    assert started == [("make", "/work")]
    assert path.read_text() == "out\nerr\n"


def test_a_killed_spawn_exits_non_zero_and_a_clean_one_zero(adapter, tmp_path):
    import asyncio
    import os

    from app.sandbox.adapter import BackgroundCommand
    from app.workspace import e2b

    class Clean:
        def wait(self):
            return type("R", (), {"exit_code": 0})()

    class Killed:
        def wait(self):
            raise RuntimeError("killed")

    async def go(handle):
        background = BackgroundCommand(id="b", cmd="x", handle=handle)
        adapter.start_background = lambda cmd, cwd=None: background
        killed: list[str] = []
        adapter.kill_command = killed.append
        fd = os.open(tmp_path / "o", os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        process = await e2b.E2BWorkspace(adapter).spawn("x", output_fd=fd)
        os.close(fd)
        process.kill_tree()
        return await process.wait(), killed

    assert asyncio.run(go(Clean())) == (0, ["b"])
    assert asyncio.run(go(Killed())) == (1, ["b"])
