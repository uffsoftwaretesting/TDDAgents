"""
Unit tests for the Workspace protocol base abstractions and error hierarchy (Part G1).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.workspace.base import (
    CommandResult,
    FileEntry,
    Workspace,
    WorkspaceAuthError,
    WorkspaceCapacityError,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspacePathError,
    WorkspaceProviderError,
    WorkspaceRateLimited,
    WorkspaceTemplateError,
    WorkspaceTimeout,
    WorkspaceTransportError,
    normalize_path,
)
from app.workspace.local import LocalWorkspace
from tests.conftest import FakeWorkspace


def test_command_result_succeeded() -> None:
    success = CommandResult(
        stdout="ok", stderr="", exit_code=0, duration=0.1, workspace="sandbox"
    )
    assert success.succeeded is True

    failure = CommandResult(
        stdout="", stderr="err", exit_code=1, duration=0.2, workspace="local"
    )
    assert failure.succeeded is False

    non_zero = CommandResult(
        stdout="", stderr="fail", exit_code=-1, duration=0.05, workspace="sandbox"
    )
    assert non_zero.succeeded is False


def test_file_entry_attributes() -> None:
    entry = FileEntry(path="src/main.py", is_dir=False, size=1024)
    assert entry.path == "src/main.py"
    assert entry.is_dir is False
    assert entry.size == 1024


def test_workspace_error_hierarchy_and_retryable_classification() -> None:
    # Non-retryable
    assert issubclass(WorkspacePathError, WorkspaceError)
    assert WorkspacePathError.retryable is False

    assert issubclass(WorkspaceNotFound, WorkspaceError)
    assert WorkspaceNotFound.retryable is False

    assert issubclass(WorkspaceAuthError, WorkspaceError)
    assert WorkspaceAuthError.retryable is False

    assert issubclass(WorkspaceCapacityError, WorkspaceError)
    assert WorkspaceCapacityError.retryable is False

    assert issubclass(WorkspaceTemplateError, WorkspaceError)
    assert WorkspaceTemplateError.retryable is False

    assert issubclass(WorkspaceProviderError, WorkspaceError)
    assert WorkspaceProviderError.retryable is False

    assert WorkspaceError.retryable is False

    # Retryable
    assert issubclass(WorkspaceTimeout, WorkspaceError)
    assert WorkspaceTimeout.retryable is True

    assert issubclass(WorkspaceRateLimited, WorkspaceError)
    assert WorkspaceRateLimited.retryable is True

    assert issubclass(WorkspaceTransportError, WorkspaceError)
    assert WorkspaceTransportError.retryable is True


def test_normalize_path_none_raises_workspace_path_error() -> None:
    with pytest.raises(WorkspacePathError) as exc_info:
        normalize_path(None)  # type: ignore[arg-type]
    assert str(exc_info.value) == "Path must not be None."


@pytest.mark.parametrize("empty_val", ["", "   ", "\t\n"])
def test_normalize_path_empty_raises_workspace_path_error(empty_val: str) -> None:
    with pytest.raises(WorkspacePathError) as exc_info:
        normalize_path(empty_val)
    assert str(exc_info.value) == "Path must not be empty."


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".", "."),
        ("./", "."),
        ("/", "."),
        ("///", "."),
        ("a", "a"),
        ("a/b/c", "a/b/c"),
        ("/a/b/c", "a/b/c"),
        ("a/b/c/", "a/b/c"),
        ("a\\b\\c", "a/b/c"),
        ("/a\\b/c\\d", "a/b/c/d"),
        ("a/./b/../c", "a/c"),
        ("a//b///c", "a/b/c"),
    ],
)
def test_normalize_path_valid_cases(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    "escaping_path",
    [
        "..",
        "../",
        "../../etc/passwd",
        "/../escaped",
        "a/b/../../../c",
    ],
)
def test_normalize_path_escapes_raise_workspace_path_error(escaping_path: str) -> None:
    with pytest.raises(WorkspacePathError) as exc_info:
        normalize_path(escaping_path)
    assert f"Path '{escaping_path}' escapes the workspace root." in str(exc_info.value)


def test_workspace_protocol_runtime_checkable(tmp_path: Path) -> None:
    fake = FakeWorkspace()
    assert isinstance(fake, Workspace)
    assert fake.kind == "sandbox"

    local = LocalWorkspace(tmp_path)
    assert isinstance(local, Workspace)
    assert local.kind == "local"

    class Incomplete:
        kind = "local"

    assert not isinstance(Incomplete(), Workspace)
