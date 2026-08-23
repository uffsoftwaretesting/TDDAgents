"""
The workspace router and DualWorkspace.

The router is the mechanism that turns an agent's declared `workspace:` field into a
concrete Workspace. In Phase 1A nothing supplies that field yet, so the assertion that
matters most here is the last one: with no spec, every resolution is the sandbox, and
Phase 1A therefore changes no execution target on its own.
"""

from __future__ import annotations

import pytest

from app.workspace.base import CommandResult, Workspace, WorkspaceNotFound
from app.workspace.router import (
    DEFAULT_SPEC,
    VALID_SPECS,
    DualWorkspace,
    resolve_workspace,
)

ROUTER_LOGGER = "TDDOrchestrator.WorkspaceRouter"


@pytest.fixture
def sandbox(make_fake_workspace):
    return make_fake_workspace("sandbox", {"shared.py": "from-sandbox"})


@pytest.fixture
def local(make_fake_workspace):
    return make_fake_workspace("local", {"shared.py": "from-local"})


# ── Resolution ───────────────────────────────────────────────────────────────

def test_no_spec_resolves_to_the_sandbox(sandbox, local):
    """Phase 1A's central guarantee: nothing reaches the host until Phase 2."""
    assert resolve_workspace(None, sandbox, local) is sandbox
    assert DEFAULT_SPEC == "sandbox"


def test_sandbox_spec_resolves_to_the_sandbox(sandbox, local):
    assert resolve_workspace("sandbox", sandbox, local) is sandbox


def test_local_spec_resolves_to_the_local_workspace(sandbox, local):
    assert resolve_workspace("local", sandbox, local) is local


def test_both_resolves_to_a_dual_workspace(sandbox, local):
    resolved = resolve_workspace("both", sandbox, local)
    assert isinstance(resolved, DualWorkspace)
    assert resolved.sandbox is sandbox
    assert resolved.local is local


@pytest.mark.parametrize("spec", [" SANDBOX ", "Local", "BOTH"])
def test_spec_is_case_and_whitespace_insensitive(spec, sandbox, local):
    assert resolve_workspace(spec, sandbox, local) is not None


def test_unknown_spec_is_rejected(sandbox, local):
    with pytest.raises(ValueError, match="Unknown workspace target"):
        resolve_workspace("everywhere", sandbox, local)


@pytest.mark.parametrize("spec", ["local", "both"])
def test_a_missing_local_workspace_is_rejected(spec, sandbox):
    """Resolving to the host without a host workspace must fail loudly, not silently."""
    with pytest.raises(ValueError, match="requires a local workspace"):
        resolve_workspace(spec, sandbox, None)


def test_sandbox_spec_does_not_need_a_local_workspace(sandbox):
    assert resolve_workspace("sandbox", sandbox) is sandbox


def test_valid_specs_are_exactly_the_three_targets():
    assert set(VALID_SPECS) == {"sandbox", "local", "both"}


# ── DualWorkspace ────────────────────────────────────────────────────────────

def test_dual_satisfies_the_workspace_protocol(sandbox, local):
    assert isinstance(DualWorkspace(sandbox, local), Workspace)


def test_dual_reads_come_from_the_sandbox(sandbox, local):
    """Sandbox-preferred reads follow from the locked 'sandbox wins during a run' rule."""
    assert DualWorkspace(sandbox, local).read_file("shared.py") == "from-sandbox"


def test_dual_execute_runs_in_the_sandbox(sandbox, local):
    result = DualWorkspace(sandbox, local).execute("echo hi")
    assert result.workspace == "sandbox"
    assert "echo hi" in sandbox.command_log
    assert local.command_log == []


def test_dual_execute_forwards_timeout_and_env(sandbox, local):
    """A dropped timeout would silently fall back to the default budget."""
    calls = []

    def record(cmd, timeout=None, env=None):
        calls.append((cmd, timeout, env))
        return CommandResult(
            stdout="", stderr="", exit_code=0, duration=0.0, workspace="sandbox"
        )

    sandbox.execute = record
    dual = DualWorkspace(sandbox, local)
    dual.execute("pytest", timeout=600, env={"K": "V"})
    dual.execute("ls")

    assert calls == [("pytest", 600, {"K": "V"}), ("ls", None, None)]


def test_unknown_spec_error_lists_the_valid_targets(sandbox, local):
    """The message is the only guidance a mistyped frontmatter field gets."""
    with pytest.raises(ValueError) as exc:
        resolve_workspace("everywhere", sandbox, local)
    assert str(exc.value) == (
        "Unknown workspace target 'everywhere'. Expected one of sandbox, local, both."
    )


def test_missing_local_error_names_the_target(sandbox):
    with pytest.raises(ValueError) as exc:
        resolve_workspace("both", sandbox, None)
    assert str(exc.value) == (
        "Workspace target 'both' requires a local workspace, but none was supplied."
    )


def test_dual_exists_and_list_consult_the_sandbox(sandbox, local):
    local.files["only-local.py"] = "x"
    dual = DualWorkspace(sandbox, local)
    assert dual.exists("shared.py") is True
    assert dual.exists("only-local.py") is False
    assert {e.path for e in dual.list_files()} == {"shared.py"}


def test_dual_list_files_forwards_path_and_depth(sandbox, local):
    """A dropped or defaulted depth would silently change how much of the tree is seen."""
    calls: list[tuple[str, int]] = []

    def record(path: str = ".", depth: int = 1) -> list:
        calls.append((path, depth))
        return []

    sandbox.list_files = record

    dual = DualWorkspace(sandbox, local)
    dual.list_files("pkg", depth=7)
    dual.list_files()

    assert calls == [("pkg", 7), (".", 1)]


def test_dual_writes_reach_both_sides(sandbox, local):
    DualWorkspace(sandbox, local).write_file("new.py", "content")
    assert sandbox.files["new.py"] == "content"
    assert local.files["new.py"] == "content"


def test_dual_deletes_reach_both_sides(sandbox, local):
    DualWorkspace(sandbox, local).delete_file("shared.py")
    assert "shared.py" not in sandbox.files
    assert "shared.py" not in local.files


def test_dual_delete_tolerates_a_missing_local_copy(sandbox, local, caplog):
    """The sandbox is authoritative; an absent local copy is not a failure."""
    del local.files["shared.py"]
    with caplog.at_level("DEBUG", logger=ROUTER_LOGGER):
        DualWorkspace(sandbox, local).delete_file("shared.py")
    assert "shared.py" not in sandbox.files
    # Tolerated, but not silent — a skipped local operation is still recorded.
    assert "Local delete" in caplog.text and "shared.py" in caplog.text


def test_dual_moves_reach_both_sides(sandbox, local):
    DualWorkspace(sandbox, local).move("shared.py", "moved.py")
    assert sandbox.files["moved.py"] == "from-sandbox"
    assert local.files["moved.py"] == "from-local"


def test_dual_move_tolerates_a_missing_local_copy(sandbox, local, caplog):
    del local.files["shared.py"]
    with caplog.at_level("DEBUG", logger=ROUTER_LOGGER):
        DualWorkspace(sandbox, local).move("shared.py", "moved.py")
    assert sandbox.files["moved.py"] == "from-sandbox"
    assert "Local move" in caplog.text and "shared.py" in caplog.text


def test_dual_move_still_raises_when_the_sandbox_copy_is_missing(sandbox, local):
    """Tolerance applies to the local side only — the sandbox is the source of truth."""
    del sandbox.files["shared.py"]
    with pytest.raises(WorkspaceNotFound, match="File not found: shared.py"):
        DualWorkspace(sandbox, local).move("shared.py", "moved.py")


def test_dual_reports_the_sandbox_as_its_kind(sandbox, local):
    """Reads and execution resolve to the sandbox, so results are labelled honestly."""
    assert DualWorkspace(sandbox, local).kind == "sandbox"
