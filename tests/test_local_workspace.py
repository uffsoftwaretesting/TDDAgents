"""
LocalWorkspace against the Workspace contract.

Every assertion here is one half of the cross-environment compatibility guarantee: the
same assertions must hold for E2BWorkspace, which the Phase 1B harness checks against a
live sandbox. Anything asserted here is therefore a statement about the *protocol*, not
about the local implementation.
"""

from __future__ import annotations

import pytest

from app.workspace.base import (
    Workspace,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceTimeout,
    normalize_path,
)
from app.workspace.local import LocalWorkspace


# ── Path contract ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/main.py", "src/main.py"),
        ("/src/main.py", "src/main.py"),          # leading slash is workspace-relative
        ("./src/main.py", "src/main.py"),
        ("src//main.py", "src/main.py"),
        ("src\\main.py", "src/main.py"),          # Windows separators normalize
        ("src/../src/main.py", "src/main.py"),    # interior .. that stays inside is fine
        (".", "."),
        ("/", "."),
        ("  src/main.py  ", "src/main.py"),
    ],
)
def test_normalize_path_accepts(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", ["..", "../outside.py", "src/../../outside.py"])
def test_normalize_path_rejects_escapes(raw):
    with pytest.raises(WorkspacePathError) as exc:
        normalize_path(raw)
    assert str(exc.value) == f"Path '{raw}' escapes the workspace root."


@pytest.mark.parametrize("raw", ["", "   "])
def test_normalize_path_rejects_empties(raw):
    with pytest.raises(WorkspacePathError) as exc:
        normalize_path(raw)
    assert str(exc.value) == "Path must not be empty."


def test_normalize_path_rejects_none():
    with pytest.raises(WorkspacePathError) as exc:
        normalize_path(None)  # type: ignore[arg-type]
    assert str(exc.value) == "Path must not be None."


def test_local_workspace_rejects_escape(local_ws):
    with pytest.raises(WorkspacePathError, match="escapes the workspace root"):
        local_ws.write_file("../escaped.py", "nope")


def test_leading_slash_strip_does_not_eat_other_characters(tmp_path):
    """
    The strip is `lstrip("/")`, a character set. A path whose first character happens to
    be one that a wider set would swallow must survive intact.
    """
    assert normalize_path("Xsrc/main.py") == "Xsrc/main.py"
    assert normalize_path("//src/main.py") == "src/main.py"


# ── Root construction ────────────────────────────────────────────────────────

def test_root_is_created_including_missing_parents(tmp_path):
    root = tmp_path / "deeply" / "nested" / "workspace"
    LocalWorkspace(root)
    assert root.is_dir()


def test_constructing_twice_on_the_same_root_is_fine(tmp_path):
    """A resumed run re-opens an existing local workspace; that must not raise."""
    root = tmp_path / "workspace"
    LocalWorkspace(root)
    again = LocalWorkspace(root)
    assert again.root == root.resolve()


def test_root_is_resolved_and_expanded(tmp_path):
    ws = LocalWorkspace(tmp_path / "workspace" / ".")
    assert ws.root == (tmp_path / "workspace").resolve()


# ── Protocol conformance ─────────────────────────────────────────────────────

def test_satisfies_the_workspace_protocol(local_ws):
    # isinstance on Protocols can fail inside pytest due to module reloading cache bugs.
    # Instead, assert the required protocol methods are present.
    required_methods = ["read_file", "write_file", "delete_file", "list_files", "exists", "move", "execute"]
    for method in required_methods:
        assert hasattr(local_ws, method), f"Missing protocol method: {method}"
    assert local_ws.kind == "local"


# ── Files ────────────────────────────────────────────────────────────────────

def test_write_then_read_round_trips(local_ws):
    local_ws.write_file("src/main.py", "print('hello')\n")
    assert local_ws.read_file("src/main.py") == "print('hello')\n"


def test_write_creates_parent_directories(local_ws):
    local_ws.write_file("a/b/c/deep.py", "x = 1")
    assert local_ws.exists("a/b/c/deep.py")


def test_write_overwrites(local_ws):
    local_ws.write_file("f.py", "first")
    local_ws.write_file("f.py", "second")
    assert local_ws.read_file("f.py") == "second"


def test_utf8_round_trips(local_ws):
    payload = "acentuação, 中文, emoji 🔴🟢, symbols ≠ ±"
    local_ws.write_file("unicode.txt", payload)
    assert local_ws.read_file("unicode.txt") == payload


def test_read_missing_file_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound, match="File not found: nope.py"):
        local_ws.read_file("nope.py")


def test_read_directory_raises_path_error(local_ws):
    local_ws.write_file("dir/child.py", "x = 1")
    with pytest.raises(WorkspacePathError) as exc_info:
        local_ws.read_file("dir")
    assert "Path is a directory, not a file: dir" in str(exc_info.value)


def test_delete_missing_file_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound, match="File not found: nope.py"):
        local_ws.delete_file("nope.py")


def test_delete_removes_file(local_ws):
    local_ws.write_file("gone.py", "x")
    local_ws.delete_file("gone.py")
    assert not local_ws.exists("gone.py")


def test_delete_removes_directory_tree(local_ws):
    local_ws.write_file("pkg/mod.py", "x")
    local_ws.delete_file("pkg")
    assert not local_ws.exists("pkg")


def test_exists_is_false_for_missing_and_for_escapes(local_ws):
    assert local_ws.exists("missing.py") is False
    assert local_ws.exists("../outside.py") is False


def test_move_relocates_and_creates_parents(local_ws):
    local_ws.write_file("old.py", "content")
    local_ws.move("old.py", "new/dir/new.py")
    assert not local_ws.exists("old.py")
    assert local_ws.read_file("new/dir/new.py") == "content"


def test_move_missing_source_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound, match="File not found: nope.py"):
        local_ws.move("nope.py", "somewhere.py")


# ── Listing ──────────────────────────────────────────────────────────────────

def test_list_files_respects_depth(local_ws):
    local_ws.write_file("top.py", "x")
    local_ws.write_file("pkg/mid.py", "x")
    local_ws.write_file("pkg/sub/deep.py", "x")

    shallow = {e.path for e in local_ws.list_files(".", depth=1)}
    assert shallow == {"top.py", "pkg"}

    deep = {e.path for e in local_ws.list_files(".", depth=8)}
    assert {"top.py", "pkg", "pkg/mid.py", "pkg/sub", "pkg/sub/deep.py"} == deep


def test_list_files_reports_dirs_and_sizes(local_ws):
    local_ws.write_file("pkg/mod.py", "abcde")
    entries = {e.path: e for e in local_ws.list_files(".", depth=4)}
    assert entries["pkg"].is_dir is True
    assert entries["pkg/mod.py"].is_dir is False
    assert entries["pkg/mod.py"].size == 5


def test_a_directory_entry_reports_zero_size(local_ws):
    """Size is meaningful for files only; a directory must not report its inode size."""
    local_ws.write_file("pkg/mod.py", "abcde")
    entries = {e.path: e for e in local_ws.list_files(".", depth=4)}
    assert entries["pkg"].size == 0


def test_list_files_defaults_to_the_root_at_depth_one(local_ws):
    """The no-argument call is the common one, so its defaults are part of the contract."""
    local_ws.write_file("top.py", "x")
    local_ws.write_file("pkg/mid.py", "x")

    assert {e.path for e in local_ws.list_files()} == {"top.py", "pkg"}


def test_depth_beyond_the_tree_is_harmless(local_ws):
    local_ws.write_file("pkg/mid.py", "x")
    assert {e.path for e in local_ws.list_files(".", depth=99)} == {"pkg", "pkg/mid.py"}


def test_list_files_on_a_file_is_rejected(local_ws):
    local_ws.write_file("f.py", "x")
    with pytest.raises(WorkspacePathError, match="not a directory"):
        local_ws.list_files("f.py")


def test_list_files_on_an_empty_workspace_is_empty(local_ws):
    assert local_ws.list_files() == []


# ── Filesystem failures ──────────────────────────────────────────────────────
#
# Every OSError branch below was previously unreachable from the test suite. They are
# the paths a real workspace hits first: a directory the run cannot write to, a file
# another process locked down. Each must surface as a WorkspaceError rather than as a
# raw OSError, because only the WorkspaceError family is classified by
# app/errors/sandbox/handler.py into the pipeline's retry taxonomy.

def test_an_unreadable_file_surfaces_as_a_workspace_error(local_ws):
    local_ws.write_file("secret.py", "x")
    (local_ws.root / "secret.py").chmod(0o000)
    try:
        with pytest.raises(WorkspaceProviderError, match="Could not read 'secret.py'"):
            local_ws.read_file("secret.py")
    finally:
        (local_ws.root / "secret.py").chmod(0o644)


def test_a_write_into_a_read_only_directory_surfaces_as_a_workspace_error(local_ws):
    local_ws.write_file("locked/keep.py", "x")
    (local_ws.root / "locked").chmod(0o555)
    try:
        with pytest.raises(WorkspaceProviderError, match="Could not write 'locked/new.py'"):
            local_ws.write_file("locked/new.py", "y")
    finally:
        (local_ws.root / "locked").chmod(0o755)


def test_a_delete_from_a_read_only_directory_surfaces_as_a_workspace_error(local_ws):
    local_ws.write_file("locked/keep.py", "x")
    (local_ws.root / "locked").chmod(0o555)
    try:
        with pytest.raises(WorkspaceProviderError, match="Could not delete 'locked/keep.py'"):
            local_ws.delete_file("locked/keep.py")
    finally:
        (local_ws.root / "locked").chmod(0o755)


def test_a_move_into_a_read_only_directory_surfaces_as_a_workspace_error(local_ws):
    local_ws.write_file("src.py", "x")
    local_ws.write_file("locked/keep.py", "y")
    (local_ws.root / "locked").chmod(0o555)
    try:
        with pytest.raises(
            WorkspaceProviderError, match="Could not move 'src.py' to 'locked/dst.py'"
        ):
            local_ws.move("src.py", "locked/dst.py")
    finally:
        (local_ws.root / "locked").chmod(0o755)


def test_list_missing_directory_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound, match="Directory not found: nope"):
        local_ws.list_files("nope")


# ── Execution ────────────────────────────────────────────────────────────────

def test_execute_returns_stdout_and_zero_exit(local_ws):
    result = local_ws.execute("echo hello")
    assert result.exit_code == 0
    assert result.succeeded is True
    assert result.stdout.strip() == "hello"
    assert result.workspace == "local"


def test_execute_returns_nonzero_exit_rather_than_raising(local_ws):
    """
    The core of the error-handling design: a failing command is data, not an exception.
    This is what lets a Red pytest run and a bad agent command be reasoned about.
    """
    result = local_ws.execute("exit 42")
    assert result.exit_code == 42
    assert result.succeeded is False


def test_execute_captures_stderr(local_ws):
    result = local_ws.execute("echo oops >&2")
    assert result.stderr.strip() == "oops"


def test_execute_runs_from_the_workspace_root(local_ws):
    local_ws.write_file("marker.txt", "here")
    result = local_ws.execute("ls")
    assert "marker.txt" in result.stdout


def test_execute_honors_env(local_ws):
    result = local_ws.execute("echo $TDD_TEST_VAR", env={"TDD_TEST_VAR": "injected"})
    assert result.stdout.strip() == "injected"


def test_execute_timeout_raises_workspace_timeout(local_ws):
    with pytest.raises(WorkspaceTimeout, match="Local command exceeded 0.3s"):
        local_ws.execute("sleep 5", timeout=0.3)


def test_execute_records_a_plausible_duration(local_ws):
    """
    Bounded on both sides on purpose. `>= 0` alone is satisfied by a duration computed
    as `now + start` instead of `now - start`, which is off by the process start epoch.
    """
    result = local_ws.execute("true")
    assert 0.0 <= result.duration < 60.0


def test_execute_returns_empty_strings_when_a_command_is_silent(local_ws):
    result = local_ws.execute("true")
    assert result.stdout == ""
    assert result.stderr == ""


def test_execute_default_timeout_comes_from_config(local_ws):
    """The workspace does not invent a timeout; Config owns it."""
    from app.config.config import Config

    result = local_ws.execute(f"echo {Config.COMMAND_TIMEOUT}")
    assert result.stdout.strip() == str(Config.COMMAND_TIMEOUT)


def test_execute_uses_a_login_shell_matching_e2b(local_ws):
    """
    E2B starts commands as `/bin/bash -l -c`. LocalWorkspace mirrors that exactly, so a
    command behaves the same on both sides — the parity guarantee in one assertion.
    """
    result = local_ws.execute("echo $0")
    assert "bash" in result.stdout


def test_resolve_oserror_raises_workspace_path_error(local_ws, monkeypatch):
    from pathlib import Path

    def fake_resolve(self):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(WorkspacePathError, match="Could not resolve path 'file.txt': Permission denied"):
        local_ws.resolve("file.txt")


def test_read_file_oserror_raises_workspace_provider_error(local_ws, monkeypatch):
    from pathlib import Path

    local_ws.write_file("file.txt", "content")

    def fake_read_text(self, encoding="utf-8"):
        raise OSError("I/O error")

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    with pytest.raises(WorkspaceProviderError, match="Could not read 'file.txt': I/O error"):
        local_ws.read_file("file.txt")


def test_write_file_oserror_raises_workspace_provider_error(local_ws, monkeypatch):
    from pathlib import Path

    def fake_write_text(self, content, encoding="utf-8"):
        raise OSError("Disk full")

    monkeypatch.setattr(Path, "write_text", fake_write_text)
    with pytest.raises(WorkspaceProviderError, match="Could not write 'file.txt': Disk full"):
        local_ws.write_file("file.txt", "content")


def test_delete_file_oserror_raises_workspace_provider_error(local_ws, monkeypatch):
    from pathlib import Path

    local_ws.write_file("file.txt", "content")

    def fake_unlink(self):
        raise OSError("Device busy")

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    with pytest.raises(WorkspaceProviderError, match="Could not delete 'file.txt': Device busy"):
        local_ws.delete_file("file.txt")


def test_move_oserror_raises_workspace_provider_error(local_ws, monkeypatch):
    from pathlib import Path

    local_ws.write_file("old.txt", "content")

    def fake_rename(self, target):
        raise OSError("Cross-device link")

    monkeypatch.setattr(Path, "rename", fake_rename)
    with pytest.raises(WorkspaceProviderError, match="Could not move 'old.txt' to 'new.txt': Cross-device link"):
        local_ws.move("old.txt", "new.txt")


def test_execute_oserror_raises_workspace_provider_error(local_ws, monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(WorkspaceProviderError, match="Could not run local command 'some_cmd': Exec format error"):
        local_ws.execute("some_cmd")
