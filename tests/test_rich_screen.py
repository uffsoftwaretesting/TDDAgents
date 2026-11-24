"""
The REPL's styled screen (2026-11-17): TDDAgents' green, questions in a box, coloured notices, the plan's
Markdown at approval, and the end-of-session boxes. A terminal that shows colour gets these; every other
sink (and every pinned test of the plain layouts) gets the plain text unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.components.design_system import theme
from app.components.design_system.question_box import PLAN_TITLE, style_question
from app.components.design_system.rich_render import plain_text, to_ansi
from app.components.logo_v2.logo_v2 import BRAND_COLOR, render_logo
from app.components.messages.system_text_message import notice
from app.components.permissions.permission_options import NO, YES
from app.components.session_summary import (
    PLAN_DONE_HINT,
    SessionReport,
    plan_done_box,
    session_report_box,
)
from app.hooks.use_can_use_tool import PermissionAnswer, PermissionPrompt, plan_approval_message
from app.query.transitions import Terminal
from app.screens.repl import Repl, question_reminder, render_permission_prompt, render_question
from tests.test_repl import FakeIO

GREEN = "38;2;134;239;172"
WARNING = "38;2;255;193;7"
GREY = "38;2;153;153;153"
DIM = "\x1b[2m"
#: The terminal's own foreground: crisp white on a dark background, crisp black on a light one.
DEFAULT_FG = "39m"
QUESTION = {"header": "Perf [size]", "question": "How large :smile: should it be?",
            "options": [{"label": "Small", "description": "A quick run"},
                        {"label": "Moderate (Recommended)", "description": "Realistic size"}]}


def _lines(ansi: str) -> list[str]:
    return plain_text(ansi).split("\n")


# ── the theme ────────────────────────────────────────────────────────────────

def test_the_brand_is_light_green_and_the_title_wears_it():
    assert theme.BRAND == BRAND_COLOR == "rgb(134,239,172)"
    assert theme.PERMISSION == theme.PLAN_MODE == theme.BRAND
    assert GREEN in render_logo("m", "/p", color=True)
    assert "\x1b[" not in render_logo("m", "/p", color=False)


def test_to_ansi_never_reads_markup_or_emoji_codes():
    from rich.text import Text

    shown = plain_text(to_ansi(Text("[bold]x[/bold] :smile:"), 40))
    assert shown == "[bold]x[/bold] :smile:"


# ── questions in a box ───────────────────────────────────────────────────────

def test_the_model_s_question_is_boxed_with_its_header_as_title():
    plain = render_question(QUESTION, 1, 2)
    styled = style_question(plain, 70)
    lines = _lines(styled)
    assert GREEN in styled and "\x1b[" in styled
    assert lines[0].startswith("╭─  ? Perf [size] (1/2)  ─")  # the header chip, brackets kept literally
    assert any("How large :smile: should it be?" in line for line in lines)  # no emoji code replaced
    body = "\n".join(lines)
    assert "1. Small" in body and "A quick run" in body and "2. Moderate (Recommended)" in body
    small = next(i for i, line in enumerate(lines) if "1. Small" in line)
    assert "A quick run" in lines[small + 1]  # the description dim beneath its label
    assert lines[-2].startswith("╰") and lines[-1] == plain.split("\n")[-1]  # the footer under the box


def test_a_permission_question_and_its_reminder_are_boxed():
    prompt = PermissionPrompt("Bash", "Run make", "", {}, (YES, NO))
    plain = render_permission_prompt(prompt)
    lines = _lines(style_question(plain, 70))
    assert lines[0].startswith("╭─  Permission needed: Bash — Run make  ─")
    assert any("1. Yes" in line for line in lines) and any("2. No" in line for line in lines)
    under = " ".join(lines[next(i for i, line in enumerate(lines) if line.startswith("╰")) + 1:])
    assert under.startswith("Choose 1-2")  # the footer, under the box (wrapped when long)
    reminder = question_reminder(plain, "Choose 1-2.")
    boxed = _lines(style_question(reminder, 70))
    assert boxed[-1] == "Choose 1-2. Esc to cancel." and boxed[0].startswith("╭─  Permission needed")


def test_the_plan_is_rendered_as_markdown_between_dashed_rules():
    plan = "# Calculator\n\nSome **bold** words.\n\n## Implementation Plan\n1. Add [x]\n2. Subtract"
    message = plan_approval_message(plan)
    prompt = PermissionPrompt("ExitPlanMode", "Exit", message, {}, (YES, NO))
    plain = render_permission_prompt(prompt)
    styled = style_question(plain, 70)
    lines = _lines(styled)
    assert lines[0].startswith(f"╭─  {PLAN_TITLE}  ─")
    assert sum("╌╌╌" in line for line in lines) == 2  # the plan between two dashed rules
    assert not any(line.strip("│ ").startswith("#") for line in lines)  # headings rendered, not raw
    assert any("Calculator" in line for line in lines) and any("Add [x]" in line for line in lines)
    assert "**" not in plain_text(styled)
    assert any("Would you like to proceed?" in line for line in lines)
    # The plain layout (what tests, headless and the reminder read) is unchanged.
    assert "# Calculator\n\nSome **bold** words." in plain


# ── notices ──────────────────────────────────────────────────────────────────

def test_notices_take_their_level_s_colour_and_stay_plain_without_colour():
    for level in ("info", "success", "warning", "error", "plan"):
        assert notice("Resumed.", level, color=False) == "Resumed."
    warned = notice("Interrupted.", "warning", color=True)
    assert plain_text(warned) == "⚠ Interrupted."
    mark, _, words = warned.partition("⚠")
    assert WARNING in mark and WARNING not in words and DEFAULT_FG in words  # only the mark is yellow
    assert plain_text(notice("⚠ already marked", "warning", color=True)) == "⚠ already marked"
    ticked = notice("✔ done", "success", color=True)
    assert "38;2;78;186;101" in ticked.partition("done")[0] and DEFAULT_FG in ticked
    info = notice("(queued for the agents)", "info", color=True)
    assert info == f"\x1b[{DEFAULT_FG}(queued for the agents)\x1b[0m"  # crisp default text, no grey, no dim
    assert GREEN in notice("⏸ plan mode on", "plan", color=True)


# ── the end of a session ─────────────────────────────────────────────────────

def test_the_finished_plan_box():
    lines = _lines(plan_done_box([("1", "Parse"), ("2", "Validate")], 60))
    assert lines[0].startswith("╭─  ✔ All sub-requirements done  ─")
    assert any("✔ 1. Parse" in line for line in lines) and any("✔ 2. Validate" in line for line in lines)
    assert any(PLAN_DONE_HINT in line for line in lines)


@pytest.mark.parametrize(("outcome", "title", "colour"), [
    ("completed", "✅ TDDAgents · session complete", GREEN),
    ("stopped", "⚠ TDDAgents · development stopped", WARNING),
    ("unplanned", "📋 TDDAgents · no approved plan", WARNING),
])
def test_the_session_report_box(outcome: str, title: str, colour: str):
    report = SessionReport(outcome=outcome, thread_id="tdd-1", project="~/p", reports="~/r/", done=2, total=3,
                           final_run="passed (exit 0)" if outcome != "unplanned" else None, tokens=(1234567, 1000),
                           stopped_on="aborted_tools" if outcome == "stopped" else None,
                           current="3. Report" if outcome == "stopped" else None,
                           plan_file="~/plan.md" if outcome == "unplanned" else None)
    styled = session_report_box(report, 80)
    text = plain_text(styled)
    assert colour in styled and text.split("\n")[0].startswith(f"╭─  {title}  ─")
    assert "Plan" in text and "2/3 sub-requirements done" in text
    assert "1,234,567 (cached 1,000)" in text and "tdd-1" in text and "~/p" in text and "~/r/" in text
    assert ("current: 3. Report" in text) == (outcome == "stopped")
    assert ("~/plan.md" in text) == (outcome == "unplanned")


# ── the REPL on a terminal that shows colour ────────────────────────────────

class ColourIO(FakeIO):
    supports_color = True


def test_a_coloured_repl_boxes_its_questions_and_still_reads_the_answer():
    async def go() -> tuple[PermissionAnswer, ColourIO]:
        io = ColourIO()
        repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace()), io)  # type: ignore[arg-type]
        asked = asyncio.create_task(repl.ask_permission(PermissionPrompt("Bash", "one", "", {}, (YES, NO))))
        await asyncio.sleep(0.01)
        await repl.handle_line("7")  # invalid: the reminder, boxed too
        await repl.handle_line("1")
        return await asked, io

    answer, io = asyncio.run(go())
    assert answer.value == "yes"
    assert GREEN in io.out[0] and plain_text(io.out[0]).startswith("╭─  Permission needed: Bash — one")
    assert plain_text(io.out[1]).endswith("Choose 1-2. Esc to cancel.")


def test_a_coloured_repl_shows_notices_and_the_finished_plan_box():
    from app.tdd.ledger import PhaseLedger, TddPhase
    from app.tdd.plan import TddPlan

    cycle = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
    done = TddPlan.from_texts(["A"]).complete_current(cycle)
    store = SimpleNamespace(get=lambda: SimpleNamespace(tdd_plan=done, tdd_paused=False), subscribe=lambda f: None)
    io = ColourIO()
    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(), store=store), io)  # type: ignore[arg-type]
    repl._turn_end(Terminal.COMPLETED)
    assert plain_text(io.out[-1]).startswith("╭─  ✔ All sub-requirements done")
    repl._turn_end(Terminal.ABORTED_TOOLS)
    assert WARNING in io.out[-1] and plain_text(io.out[-1]) == "⚠ Interrupted. Tell the agents what to do next."
    plain = FakeIO()
    run: Any = SimpleNamespace(on_message=None, ctx=SimpleNamespace(), store=store)
    Repl(run, plain)._turn_end(Terminal.COMPLETED)
    assert plain.out[-1] == "✔ Every sub-requirement is done. Add more with /plan add, or /exit."


# ── the exit report ──────────────────────────────────────────────────────────

def test_the_exit_report_is_one_box_on_a_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    import app.main as entry
    from app.cli.print import DevelopmentOutcome
    from app.tdd.plan import TddPlan

    monkeypatch.chdir(tmp_path)

    class Workspace:
        root = tmp_path / "proj"

    outcome = DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=TddPlan())
    entry.report("tdd-x", Workspace(), "req", outcome, color=True)
    out = plain_text(capsys.readouterr().out)
    assert "📋 TDDAgents · no approved plan" in out and "tdd-x" in out and "Tokens" in out
    assert "📁 Project:" not in out  # the emoji lines are the plain form only
    entry.report("tdd-x", Workspace(), "req", outcome)
    assert "📁 Project:" in capsys.readouterr().out


def test_a_record_marked_off_terminal_never_prints():
    from app.utils.debug import OFF_TERMINAL, TerminalLogFilter

    record = logging.makeLogRecord({"name": "TDDMain", "levelno": logging.INFO, "msg": "x", **OFF_TERMINAL})
    assert not TerminalLogFilter().filter(record)
    assert TerminalLogFilter().filter(logging.makeLogRecord({"name": "TDDMain", "levelno": logging.INFO}))


def test_question_reminder_reads_the_plain_text_only():
    plain = render_question(QUESTION, 1, 1)
    assert question_reminder(plain, plain.split("\n")[-1]).startswith("? [Perf [size]] How large")
    assert all("\x1b" not in part for part in (plain, question_reminder(plain, "x")))


# ── crisp text and full width (2026-11-17) ──────────────────────────────────

def test_no_text_is_grey_or_dim_only_the_marks_are_coloured():
    from app.components.design_system.question_box import style_question as box

    styled = [
        box(render_question(QUESTION, 1, 2), 80),
        box(render_permission_prompt(PermissionPrompt("Bash", "Run make", "", {}, (YES, NO))), 80),
        box(render_permission_prompt(PermissionPrompt("ExitPlanMode", "Exit", plan_approval_message("# P\n1. A"),
                                                      {}, (YES, NO))), 80),
        plan_done_box([("1", "Parse")], 80),
        session_report_box(SessionReport(outcome="completed", thread_id="t", project="~/p", reports="~/r/",
                                         done=1, total=1, final_run="passed", tokens=(1, 0)), 80),
        notice("(queued for the agents)", "info", color=True),
        render_logo("m", "/p", color=True),
    ]
    for ansi in styled:
        assert GREY not in ansi and DIM not in ansi
        assert DEFAULT_FG in ansi  # the text is the terminal's own foreground
    assert all(GREEN in ansi for ansi in styled[:5])  # the boxes keep their green border and title


def test_boxes_span_the_terminal_width():
    from app.components.design_system.question_box import style_question as box

    lines = _lines(box(render_question(QUESTION, 1, 1), 150))
    boxed = [line for line in lines if line.startswith(("╭", "│", "╰"))]
    assert boxed and all(len(line) == 150 for line in boxed)
    assert all(len(line) == 150 for line in _lines(plan_done_box([("1", "x")], 150)))


def test_the_repl_uses_the_full_terminal_width(monkeypatch: pytest.MonkeyPatch):
    import os

    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace()), FakeIO())  # type: ignore[arg-type]
    monkeypatch.setattr("app.screens.repl.shutil.get_terminal_size", lambda *a, **k: os.terminal_size((160, 40)))
    assert repl._width() == 160
    monkeypatch.setattr("app.screens.repl.shutil.get_terminal_size", lambda *a, **k: os.terminal_size((20, 40)))
    assert repl._width() == 40
