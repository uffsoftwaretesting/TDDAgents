"""
Relative paths after a `cd`: `expandPath`, the file tools' `backfillObservableInput`, the Bash
permission and TDD checks, and `resetCwdIfOutsideProject` (user decision 2026-11-01: Bash and the
file tools follow the tracked working directory).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolCall

from app.bootstrap.state import set_cwd
from app.services.tools.tool_execution import run_tool_use
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.bash_phase_permissions import check_bash_tdd_phase
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tool import ToolResult, build_tool, tool_context_for
from app.tools.bash_tool.bash_tool import bash_permission_inputs
from app.tools.bash_tool.utils import (
    reset_cwd_if_outside_project,
    std_err_append_shell_reset_message,
)
from app.tools.file_read_tool.file_read_tool import build_read_file_tool
from app.tools.file_write_tool.file_write_tool import build_write_file_tool
from app.tools.glob_tool.glob_tool import build_glob_tool
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.path import backfill_path, expand_path, tracked_cwd_offset
from tests.conftest import FakeWorkspace
from tests.plan_helpers import CURRENT_ITEM_PLAN


def _ctx(root: str, perm: ToolPermissionContext | None = None, **state: Any) -> Any:
    ws = FakeWorkspace()
    ws.root = root  # type: ignore[attr-defined]
    app = AppState(tool_permission_context=perm or ToolPermissionContext(), **state)
    return tool_context_for(AppStateStore(app), workspace=ws)


def test_expand_path_is_unchanged_until_the_shell_moves():
    ctx = _ctx("/p")
    assert tracked_cwd_offset(ctx) is None
    assert expand_path("a.py", ctx) == "a.py"
    set_cwd(ctx.workspace, "/p/src")
    assert tracked_cwd_offset(ctx) == "src"
    assert [expand_path(p, ctx) for p in ("a.py", "../b.py", "/abs/c.py", "~/d.py", "")] == [
        "src/a.py", "b.py", "/abs/c.py", "~/d.py", ""]
    set_cwd(ctx.workspace, "/elsewhere/x")
    assert tracked_cwd_offset(ctx) == "/elsewhere/x"
    assert expand_path("a.py", ctx) == "/elsewhere/x/a.py"


def test_backfill_path_fills_the_path_and_the_search_default():
    ctx = _ctx("/p")
    file_path, search = backfill_path("file_path"), backfill_path("path", default_to_cwd=True)
    unchanged: dict[str, Any] = {"pattern": "*"}
    search(unchanged, ctx)
    assert unchanged == {"pattern": "*"}
    set_cwd(ctx.workspace, "/p/src")
    args: dict[str, Any] = {"file_path": "a.py"}
    file_path(args, ctx)
    no_path: dict[str, Any] = {"file_path": None}
    file_path(no_path, ctx)
    given, default = {"path": "lib"}, {"pattern": "*"}
    search(given, ctx)
    search(default, ctx)
    assert (args, no_path, given, default) == ({"file_path": "src/a.py"}, {"file_path": None},
                                               {"path": "src/lib"}, {"pattern": "*", "path": "src"})


def test_the_pipeline_hands_hooks_permissions_and_the_call_the_backfilled_input():
    seen: list[dict[str, Any]] = []

    async def call(args: dict[str, Any], ctx: Any) -> ToolResult:
        seen.append(dict(args))
        return ToolResult(content="ok")

    tool = build_tool(name="T", prompt="p", call=call, backfill_observable_input=backfill_path("file_path"))
    ctx = _ctx("/p")
    set_cwd(ctx.workspace, "/p/src")
    asyncio.run(run_tool_use(ToolCall(name="T", args={"file_path": "a.py"}, id="c1"), AIMessage(content=""), ctx,
                             (tool,)))
    assert seen == [{"file_path": "src/a.py"}]


def test_file_tools_read_and_write_where_the_shell_is():
    ctx = _ctx("/p", ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS))
    ctx.workspace.files["src/a.py"] = "x = 1\n"
    set_cwd(ctx.workspace, "/p/src")
    read = asyncio.run(run_tool_use(ToolCall(name="ReadFile", args={"file_path": "a.py"}, id="r"),
                                    AIMessage(content=""), ctx, (build_read_file_tool(),)))
    assert "x = 1" in str(read.message.content)
    asyncio.run(run_tool_use(ToolCall(name="WriteFile", args={"file_path": "b.py", "content": "y"}, id="w"),
                             AIMessage(content=""), ctx, (build_write_file_tool(),)))
    assert ctx.workspace.files["src/b.py"] == "y"
    glob = build_glob_tool()
    args: dict[str, Any] = {"pattern": "*.py"}
    glob.backfill_observable_input(args, ctx)
    assert args["path"] == "src"


def test_bash_permissions_resolve_against_the_shell_and_keep_the_root():
    ctx = _ctx("/p", ToolPermissionContext(additional_working_directories=("/extra",)))
    perm, cwd = bash_permission_inputs(ctx)
    assert (cwd, perm.additional_working_directories) == ("/p", ("/extra",))
    set_cwd(ctx.workspace, "/p/src")
    perm, cwd = bash_permission_inputs(ctx)
    assert (cwd, perm.additional_working_directories) == ("/p/src", ("/p", "/extra"))
    rooted = _ctx("/p", ToolPermissionContext(additional_working_directories=("/p",)))
    set_cwd(rooted.workspace, "/p/src")
    assert bash_permission_inputs(rooted)[0].additional_working_directories == ("/p",)


def test_a_redirect_is_judged_where_it_lands():
    red = PhaseLedger(phase=TddPhase.RED)
    assert check_bash_tdd_phase("echo x > test_a.py", red, "tests")[0] is True
    assert check_bash_tdd_phase("echo x > a.py", red, "tests")[0] is True  # tests/a.py is a test path
    assert check_bash_tdd_phase("echo x > a.py", red, None)[0] is False
    assert check_bash_tdd_phase("echo x > /tmp/log", red, "tests")[0] is False
    assert check_bash_tdd_phase("echo x > ~/log", red, "tests")[0] is False


def test_the_tdd_tier_uses_the_shell_directory_for_bash():
    from app.tools.bash_tool.bash_tool import build_bash_tool
    from app.utils.permissions.permissions import tdd_phase_denial

    ctx = _ctx("/p", tdd_plan=CURRENT_ITEM_PLAN, phase_ledger=PhaseLedger(phase=TddPhase.RED))
    command = {"command": "echo x > a.py"}
    assert tdd_phase_denial(build_bash_tool(), command, ctx) is not None
    set_cwd(ctx.workspace, "/p/tests")
    assert tdd_phase_denial(build_bash_tool(), command, ctx) is None


@pytest.mark.parametrize("maintain", [False, True])
def test_reset_cwd_if_outside_project(maintain, monkeypatch, tmp_path):
    root = str(tmp_path)
    (tmp_path / "sub").mkdir()
    if maintain:
        monkeypatch.setenv("TDDAGENTS_BASH_MAINTAIN_PROJECT_WORKING_DIR", "1")
    else:
        monkeypatch.delenv("TDDAGENTS_BASH_MAINTAIN_PROJECT_WORKING_DIR", raising=False)
    ctx = _ctx(root)
    from app.utils.cwd import get_cwd

    assert reset_cwd_if_outside_project(ctx) is False and get_cwd(ctx) == root
    set_cwd(ctx.workspace, str(tmp_path / "sub"))
    assert reset_cwd_if_outside_project(ctx) is False
    assert get_cwd(ctx) == (root if maintain else str(tmp_path / "sub"))
    set_cwd(ctx.workspace, "/")
    assert reset_cwd_if_outside_project(ctx) is (not maintain)
    assert get_cwd(ctx) == root
    assert std_err_append_shell_reset_message("  oops \n", ctx) == f"oops\nShell cwd was reset to {root}"
    assert std_err_append_shell_reset_message("", ctx) == f"\nShell cwd was reset to {root}"
    assert Path(root).is_dir()
