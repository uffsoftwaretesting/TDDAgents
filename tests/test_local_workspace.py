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
    WorkspaceTimeout,
    normalize_path,
)


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


@pytest.mark.parametrize("raw", ["..", "../outside.py", "src/../../outside.py", "", "   "])
def test_normalize_path_rejects_escapes_and_empties(raw):
    with pytest.raises(WorkspacePathError):
        normalize_path(raw)


def test_local_workspace_rejects_escape(local_ws):
    with pytest.raises(WorkspacePathError):
        local_ws.write_file("../escaped.py", "nope")


# ── Protocol conformance ─────────────────────────────────────────────────────

def test_satisfies_the_workspace_protocol(local_ws):
    assert isinstance(local_ws, Workspace)
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
    with pytest.raises(WorkspaceNotFound):
        local_ws.read_file("nope.py")


def test_delete_missing_file_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound):
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
    with pytest.raises(WorkspaceNotFound):
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


def test_list_missing_directory_raises_not_found(local_ws):
    with pytest.raises(WorkspaceNotFound):
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
    with pytest.raises(WorkspaceTimeout):
        local_ws.execute("sleep 5", timeout=0.3)


def test_execute_records_duration(local_ws):
    result = local_ws.execute("true")
    assert result.duration >= 0.0
