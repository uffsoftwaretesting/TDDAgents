"""
The REPL's screen end to end (2026-11-14): whole sessions with a scripted model and the real tools, the
real `RunTests` over a pytest project, read off the terminal the REPL writes to.

What it pins, as the user decided it:

* each call prints as upstream's row (`⏺ Write(tests/test_calc.py)`), with its result right under it,
  once that result arrives; parallel calls each keep their own result;
* Write's result is upstream's condensed line (`Wrote N lines to <path>`), the content with `--verbose`;
* RunTests' result is its verdict, with the phase the run was judged in (RED, GREEN, REFACTOR);
* no raw arguments, `<tool_use_error>` tags or timestamped log lines reach the screen;
* the status line names the running tool while it runs, and is gone when the turn ends;
* the settings notice is written once while the errors stay.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from app.cli.print import open_session_run
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt, build_interactive_can_use_tool
from app.query.transitions import Terminal
from app.screens.repl import Repl
from app.tdd.plan import TddPlan
from app.types.permissions import PermissionMode, PermissionRuleSource, ToolPermissionContext
from tests.test_repl import CALC_IMPL, CALC_TEST, FakeIO, Model, call, cycle_turns, deps_for, until


async def _session(workspace: Any, tmp_path: Path, model: Model, plan: TddPlan,
                   script: Callable[[Repl, FakeIO], Any], *, verbose: bool = False
                   ) -> tuple[Terminal | None, Repl, FakeIO]:
    io = FakeIO()
    holder: dict[str, Repl] = {}

    async def ask(prompt: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(prompt)

    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=False)
    async with open_session_run(run_id=f"loop-screen-{id(model)}", plan=plan, workspace=workspace,
                                deps=deps_for(model), permission_context=perm, project_root=tmp_path,
                                home=tmp_path / "home", managed_dir=tmp_path / "etc",
                                can_use_tool=build_interactive_can_use_tool(ask)) as run:
        repl = Repl(run, io, verbose=verbose)
        holder["repl"] = repl
        runner = asyncio.create_task(repl.run_session("start", "resume"))
        await script(repl, io)
        terminal = await asyncio.wait_for(runner, 60)
    return terminal, repl, io


async def _until_done(repl: Repl, io: FakeIO) -> None:
    await until(lambda: "Every sub-requirement is done" in io.text(), timeout=60)
    io.lines.put_nowait("/exit")


def _rows(io: FakeIO) -> list[str]:
    return [line for text in io.out for line in text.split("\n")]


def _result_under(rows: list[str], row: str) -> list[str]:
    """The result lines printed right under each occurrence of `row`."""
    return [rows[i + 1] for i, line in enumerate(rows) if line == row and i + 1 < len(rows)]


def test_a_cycle_reads_as_rows_with_their_results(python_workspace, tmp_path):
    terminal, repl, io = asyncio.run(_session(python_workspace, tmp_path, Model(cycle_turns()),
                                              TddPlan.from_texts(["add"]), _until_done))
    assert terminal == Terminal.COMPLETED and repl.plan.is_complete
    rows = _rows(io)
    test_lines = len(CALC_TEST.splitlines())
    assert _result_under(rows, "⏺ Write(tests/test_calc.py)") == [
        f"  ⎿  Wrote {test_lines} lines to tests/test_calc.py"]
    assert _result_under(rows, "⏺ Write(calculator.py)") == [
        f"  ⎿  Wrote {len(CALC_IMPL.splitlines())} lines to calculator.py"]
    verdicts = _result_under(rows, "⏺ RunTests")
    assert len(verdicts) == 3
    red, green, refactor = verdicts
    assert re.fullmatch(r"  ⎿  exit [1-9]\d* · .+ · RED", red), red  # the test cannot import its code yet
    assert re.fullmatch(r"  ⎿  exit 0 · 1 test passed · coverage \d+\.\d% · GREEN", green), green
    assert re.fullmatch(r"  ⎿  exit 0 · 1 test passed · coverage \d+\.\d% · REFACTOR", refactor), refactor
    screen = io.text()
    assert not re.search(r"⏺ \w+\(\w+=", screen)  # no raw `name=value` arguments
    assert "<tool_use_error>" not in screen and "[INFO]" not in screen and "[WARNING]" not in screen
    assert repl.status_line() is None  # the turn is over: no status line
    assert repl.renderer.running_calls() == []


def test_verbose_shows_what_was_written(python_workspace, tmp_path):
    _, _, io = asyncio.run(_session(python_workspace, tmp_path, Model(cycle_turns()),
                                    TddPlan.from_texts(["add"]), _until_done, verbose=True))
    rows = _rows(io)
    start = next(i for i, row in enumerate(rows) if row.startswith("⏺ Write(") and row.endswith("test_calc.py)"))
    assert rows[start + 1].startswith("  ⎿  Wrote 4 lines to ") and rows[start + 1].endswith("test_calc.py")
    assert rows[start + 2:start + 2 + 4] == ["     " + line for line in CALC_TEST.splitlines()]


def test_parallel_calls_each_keep_their_own_result(python_workspace, tmp_path):
    both = AIMessage(content="", tool_calls=[
        {"name": "WriteFile", "args": {"file_path": "tests/test_calc.py", "content": CALC_TEST}, "id": "w1"},
        {"name": "WriteFile", "args": {"file_path": "notes.txt", "content": "a\nb\nc\n"}, "id": "w2"}])
    turns = [[both], *cycle_turns()[1:]]
    _, _, io = asyncio.run(_session(python_workspace, tmp_path, Model(turns), TddPlan.from_texts(["add"]),
                                    _until_done))
    rows = _rows(io)
    assert _result_under(rows, "⏺ Write(tests/test_calc.py)") == ["  ⎿  Wrote 4 lines to tests/test_calc.py"]
    # The second call is refused (RED locks a non-test file): its error stays under its own row.
    assert _result_under(rows, "⏺ Write(notes.txt)") == [
        "  ⎿  Error: TDD phase RED denies writing to production file 'notes.txt'. "
        "Only test files may be written in RED phase."]


def test_a_refused_call_reads_without_tags(python_workspace, tmp_path):
    # In RED a production file is locked: the write is refused, and the refusal reads as upstream's error line.
    turns = [[call("WriteFile", "x1", file_path="calculator.py", content=CALC_IMPL)], *cycle_turns()]
    _, _, io = asyncio.run(_session(python_workspace, tmp_path, Model(turns), TddPlan.from_texts(["add"]),
                                    _until_done))
    rows = _rows(io)
    first = _result_under(rows, "⏺ Write(calculator.py)")[0]
    assert first.startswith("  ⎿  Error: ") and "<tool_use_error>" not in first


def test_the_status_line_names_the_running_tool(python_workspace, tmp_path):
    seen: list[str] = []

    async def script(repl: Repl, io: FakeIO) -> None:
        def running_tests() -> bool:
            line = repl.status_line()
            if line is not None:
                seen.append(line)
            return line is not None and line.startswith("⛨ RunTests…")
        await until(running_tests, timeout=60)
        await _until_done(repl, io)

    _, repl, _ = asyncio.run(_session(python_workspace, tmp_path, Model(cycle_turns()),
                                      TddPlan.from_texts(["add"]), script))
    assert any(line == "⛨ RunTests… (esc to interrupt)" for line in seen)
    assert repl.status_line() is None


def test_the_settings_notice_is_written_once_while_the_errors_stay(python_workspace, tmp_path):
    (tmp_path / ".mcp.json").write_text("not json")

    notified: list[int] = []

    async def script(repl: Repl, io: FakeIO) -> None:
        detector = repl.run.settings_change_detector
        assert detector is not None
        await until(lambda: "Found 1 settings issue" in io.text())
        for _ in range(3):  # three settings changes while the error stays
            detector.notify_change(PermissionRuleSource.PROJECT_SETTINGS)
            notified.append(1)
        await _until_done(repl, io)

    _, _, io = asyncio.run(_session(python_workspace, tmp_path, Model(cycle_turns()),
                                    TddPlan.from_texts(["add"]), script))
    assert len(notified) == 3
    assert io.text().count("Found 1 settings issue · /doctor for details") == 1
