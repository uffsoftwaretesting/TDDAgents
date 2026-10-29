"""
Phase 11: the interactive development session (`app/screens/repl.py`), its slash commands
(`app/commands.py`) and `develop_interactive`, against a fake terminal and a scripted model.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Self

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.cli.print import open_session_run
from app.commands import (
    HELP_TEXT,
    CommandError,
    SlashCommand,
    apply_plan_command,
    parse_slash_command,
)
from app.components.permissions.permission_options import NO, YES, PermissionOption
from app.hooks.use_can_use_tool import (
    PermissionAnswer,
    PermissionPrompt,
    build_interactive_can_use_tool,
)
from app.query.transitions import Terminal
from app.screens.repl import (
    PROMPT,
    Repl,
    parse_permission_answer,
    render_message,
    render_permission_prompt,
    turn_end_text,
)
from app.session.prompts import (
    development_continue_prompt,
    development_replanned_prompt,
)
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.plan import ItemStatus, PlanError, TddPlan
from app.types.permissions import PermissionMode, ToolPermissionContext

CYCLE = PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True, refactor_passed=True)
CALC_TEST = "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n"
CALC_IMPL = "def add(a, b):\n    return a + b\n"


# ── the pieces ───────────────────────────────────────────────────────────────

def test_parse_slash_command():
    assert parse_slash_command("/plan add X  ") == SlashCommand("plan", "add X")
    assert parse_slash_command("  /exit") == SlashCommand("exit")
    assert parse_slash_command("/unknown x") is None
    assert parse_slash_command("hello") is None
    assert SlashCommand("plan").immediate and not SlashCommand("plan", "add x").immediate
    assert SlashCommand("pause").immediate and not SlashCommand("requirements", "x").immediate
    assert not SlashCommand("resume").immediate


def test_apply_plan_command():
    plan = TddPlan.from_texts(["A", "B"])
    assert [i.text for i in apply_plan_command(plan, "add C").items] == ["A", "B", "C"]
    assert apply_plan_command(plan, "edit 2 B two").items[1].text == "B two"
    assert [i.id for i in apply_plan_command(plan, "remove 1").items] == ["2"]
    assert [i.id for i in apply_plan_command(plan, "move 2 1").items] == ["2", "1"]


@pytest.mark.parametrize("args, message", [
    ("add", "Usage: /plan add <text>"),
    ("edit 1", "Usage: /plan edit <id> <text>"),
    ("edit", "Usage: /plan edit <id> <text>"),
    ("remove", "Usage: /plan remove <id>"),
    ("remove 1 2", "Usage: /plan remove <id>"),
    ("move 1", "Usage: /plan move <id> <position>"),
    ("move 1 x", "Usage: /plan move <id> <position>"),
    ("remove 9", "No sub-requirement with id '9'."),
    ("frobnicate", "Usage: /plan [add <text> | edit <id> <text> | remove <id> | move <id> <position>]"),
])
def test_apply_plan_command_errors(args: str, message: str):
    with pytest.raises(CommandError) as exc:
        apply_plan_command(TddPlan.from_texts(["A"]), args)
    assert str(exc.value) == message
    assert not isinstance(exc.value, PlanError)


OPTIONS = (YES, PermissionOption("yes-session", "Yes, during this session"), NO)


@pytest.mark.parametrize("line, answer", [
    ("1", PermissionAnswer("yes")),
    (" 2 ", PermissionAnswer("yes-session")),
    ("3", PermissionAnswer("no")),
    ("3 use the helper", PermissionAnswer("no", "use the helper")),
    ("do it differently", PermissionAnswer("no", "do it differently")),
    ("", None),
    ("4", None),
    ("0", None),
])
def test_parse_permission_answer(line: str, answer: PermissionAnswer | None):
    assert parse_permission_answer(OPTIONS, line) == answer


def test_text_with_no_reject_option_is_still_a_no():
    assert parse_permission_answer((YES,), "nah") == PermissionAnswer("no", "nah")


def test_render_permission_prompt():
    prompt = PermissionPrompt("Bash", "Run make", "needs approval", {}, OPTIONS)
    assert render_permission_prompt(prompt) == (
        "⚠ Permission needed: Bash — Run make\nneeds approval\n  1. Yes\n  2. Yes, during this session\n  3. No\n"
        "Choose 1-3; after a No you may add what to do instead (e.g. \"3 use the existing helper\").")
    assert "needs approval" not in render_permission_prompt(dataclasses.replace(prompt, message=""))


def test_render_message():
    call = AIMessage(content="Writing it", tool_calls=[{"id": "1", "name": "WriteFile", "args": {"file_path": "a.py"}}])
    assert render_message(call) == "● Writing it\n⏺ WriteFile(file_path='a.py')"
    assert render_message(AIMessage(content=[{"type": "text", "text": "hi"}, {"type": "other"}])) == "● hi"
    assert render_message(AIMessage(content="  ")) is None
    assert render_message(ToolMessage(content="ok\nmore", tool_call_id="1")) == "  ⎿ ok"
    assert render_message(ToolMessage(content="", tool_call_id="1")) == "  ⎿ (no output)"
    assert render_message(ToolMessage(content="bad", tool_call_id="1", status="error")) == "  ✗ bad"
    assert render_message(ToolMessage(content="x" * 300, tool_call_id="1")) == "  ⎿ " + "x" * 200
    assert render_message(HumanMessage(content="reminder", additional_kwargs={"is_meta": True})) is None
    assert render_message(HumanMessage(content="you")) is None
    assert render_message("result") is None


def test_turn_end_text():
    plan = TddPlan.from_texts(["A", "B"])
    assert turn_end_text(Terminal.COMPLETED, plan.complete_current(CYCLE).complete_current(CYCLE), False) == (
        "✔ Every sub-requirement is done. Add more with /plan add, or /exit.")
    assert turn_end_text(Terminal.ABORTED_TOOLS, plan, False) == "Interrupted. Tell the agents what to do next."
    assert turn_end_text(Terminal.ABORTED_TOOLS, plan.complete_current(CYCLE).complete_current(CYCLE), False) == (
        "Interrupted. Tell the agents what to do next.")
    assert turn_end_text(Terminal.ABORTED_STREAMING, plan, False).startswith("Interrupted.")
    assert turn_end_text(Terminal.STOP_HOOK_PREVENTED, plan, True) == (
        "Paused: 0/2 done — current: 1. A. Type to continue, /resume, or /help.")
    assert turn_end_text(Terminal.MODEL_ERROR, plan, False) == (
        "Stopped (model_error): 0/2 done — current: 1. A. Type to continue, /resume, or /help.")
    assert turn_end_text(Terminal.COMPLETED, TddPlan(), False) == (
        "Stopped (completed): no plan. Type to continue, /resume, or /help.")


# ── the session ──────────────────────────────────────────────────────────────

class FakeIO:
    """A terminal: the test types lines into a queue; what the REPL writes is collected."""

    def __init__(self) -> None:
        self.lines: asyncio.Queue[str | None] = asyncio.Queue()
        self.out: list[str] = []
        self.prompts: list[str] = []

    async def read_line(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return await self.lines.get()

    def write(self, text: str) -> None:
        self.out.append(text)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def text(self) -> str:
        return "\n".join(self.out)


class Model:
    """A scripted model: each turn is a list of messages, or an async function of the state."""

    def __init__(self, turns: list[Any]) -> None:
        self.turns = list(turns)
        self.seen: list[Any] = []

    async def __call__(self, state: Any, config: Any) -> AsyncIterator[Any]:
        self.seen.append(state)
        if not self.turns and not state.tool_context.get_app_state().tdd_plan.is_complete:
            # Out of script with work left: the stop hook would send it back forever (Phase 12),
            # so the scripted user presses Esc.
            state.tool_context.cancel.cancel()
        turn = self.turns.pop(0) if self.turns else [AIMessage(content="stop")]
        if callable(turn):
            turn = await turn(state)
        for message in turn:
            yield message


def call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def cycle_turns(n: int = 1) -> list[Any]:
    return [
        [call("WriteFile", f"w{n}", file_path="tests/test_calc.py", content=CALC_TEST)],
        [call("RunTests", f"r{n}", test_path="tests")],
        [call("WriteFile", f"i{n}", file_path="calculator.py", content=CALC_IMPL)],
        [call("RunTests", f"g{n}", test_path="tests")],
        [call("RunTests", f"f{n}", test_path="tests")],
        [AIMessage(content="done")],
    ]


async def until(predicate: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never held")
        await asyncio.sleep(0.01)


def deps_for(model: Model) -> Any:
    from app.query.deps import get_production_deps

    return dataclasses.replace(get_production_deps(), call_model=model)


async def session(workspace: Any, tmp_path: Path, model: Model, plan: TddPlan,
                  script: Callable[[Repl, FakeIO], Any], *, rescope: Any = None,
                  mode: PermissionMode = PermissionMode.ACCEPT_EDITS) -> tuple[Terminal | None, Repl, FakeIO]:
    io = FakeIO()
    holder: dict[str, Repl] = {}

    async def ask(prompt: PermissionPrompt) -> PermissionAnswer:
        return await holder["repl"].ask_permission(prompt)

    perm = ToolPermissionContext(mode=mode, should_avoid_permission_prompts=False)
    async with open_session_run(run_id=f"loop-repl-{id(model)}", plan=plan, workspace=workspace,
                                deps=deps_for(model), permission_context=perm, project_root=tmp_path,
                                home=tmp_path / "home", managed_dir=tmp_path / "etc",
                                can_use_tool=build_interactive_can_use_tool(ask)) as run:
        repl = Repl(run, io, specification="SPEC", rescope=rescope)
        holder["repl"] = repl
        runner = asyncio.create_task(repl.run_session("start", "resume"))
        await script(repl, io)
        terminal = await asyncio.wait_for(runner, 30)
    return terminal, repl, io


def test_a_session_works_through_the_plan_and_stays_open(python_workspace, tmp_path):
    model = Model(cycle_turns())

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: "Every sub-requirement is done" in io.text())
        io.lines.put_nowait("/exit")

    terminal, repl, io = asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["add"]), script))
    assert terminal == Terminal.COMPLETED
    assert repl.plan.is_complete
    assert io.out[0].startswith("TDD plan:\n▶ 1. add\n\n" + HELP_TEXT)
    assert "⏺ RunTests(test_path='tests')" in io.text()
    assert io.prompts and io.prompts[0] == PROMPT
    assert model.seen[0].messages[0].content == "start"


def test_a_line_typed_while_the_agents_work_is_queued_for_them(python_workspace, tmp_path):
    release = asyncio.Event()

    async def slow(state: Any) -> list[Any]:
        await release.wait()
        return [call("RunTests", "r1", test_path="tests")]

    model = Model([slow, [AIMessage(content="noted")], [AIMessage(content="ok")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.busy and len(model.seen) == 1)
        io.lines.put_nowait("please also add docstrings")
        await until(lambda: "(queued for the agents)" in io.text())
        release.set()
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    later = [str(m.content) for state in model.seen[1:] for m in state.messages]
    assert any("please also add docstrings" in text for text in later)


def test_esc_interrupts_the_turn_and_the_next_one_runs(python_workspace, tmp_path):
    started = asyncio.Event()

    async def runs_until_cancelled(state: Any) -> list[Any]:
        started.set()
        while not state.tool_context.cancel.cancelled:
            await asyncio.sleep(0.01)
        return [AIMessage(content="cut off")]

    model = Model([runs_until_cancelled, [AIMessage(content="back")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await started.wait()
        repl.interrupt()
        await until(lambda: not repl.busy)
        io.lines.put_nowait("carry on")
        await until(lambda: len(model.seen) == 2 and not repl.busy)
        io.lines.put_nowait("/exit")

    terminal, _, _ = asyncio.run(session(
        python_workspace, tmp_path, model, TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    assert model.seen[1].tool_context.cancel.cancelled is False
    assert terminal == Terminal.COMPLETED


def test_a_permission_question_is_answered_by_the_next_line(python_workspace, tmp_path):
    model = Model([[call("Bash", "b1", command="make build")], [AIMessage(content="ran it")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: "Permission needed: Bash" in io.text())
        io.lines.put_nowait("")  # asks again
        await until(lambda: "Choose 1-3." in io.out)
        io.lines.put_nowait("1")
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    results = [m for m in model.seen[1].messages if isinstance(m, ToolMessage)]
    assert results and "Permission" not in str(results[-1].content)


def test_esc_during_a_question_rejects_and_ends_the_turn(python_workspace, tmp_path):
    model = Model([[call("Bash", "b1", command="make build")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.pending is not None)
        repl.interrupt()
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    terminal, _, io = asyncio.run(session(python_workspace, tmp_path, model,
                                          TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    assert terminal == Terminal.ABORTED_TOOLS
    assert "Interrupted." in io.text()


def test_plan_commands_and_their_deferral(python_workspace, tmp_path):
    release = asyncio.Event()

    async def slow(state: Any) -> list[Any]:
        await release.wait()
        return [AIMessage(content="stop")]

    model = Model([slow])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.busy)
        io.lines.put_nowait("/plan add B")
        await until(lambda: "(/plan runs when the current turn ends)" in io.out)
        io.lines.put_nowait("/plan")
        await until(lambda: any(line.startswith("▶ 1. A") for line in io.out))
        assert [i.text for i in repl.plan.items] == ["A"]
        io.lines.put_nowait("/pause")
        release.set()
        await until(lambda: [i.text for i in repl.plan.items] == ["A", "B"])
        io.lines.put_nowait("/plan bogus")
        await until(lambda: any(line.startswith("Usage: /plan [") for line in io.out))
        io.lines.put_nowait("/help")
        await until(lambda: io.out.count(HELP_TEXT) == 1)
        io.lines.put_nowait("   ")
        io.lines.put_nowait("/exit")

    terminal, _, io = asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]), script))
    assert terminal == Terminal.STOP_HOOK_PREVENTED
    assert "Paused: the agents will stop at the end of this turn. /resume to continue." in io.out
    # the turn ends (and is reported) before the deferred /plan add runs
    assert "Paused: 0/1 done — current: 1. A. Type to continue, /resume, or /help." in io.out


def test_resume_unpauses_and_continues(python_workspace, tmp_path):
    model = Model([[AIMessage(content="stopping")], [AIMessage(content="again")], [AIMessage(content="x")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: not repl.busy and repl.last_terminal is not None)
        io.lines.put_nowait("/resume")
        await until(lambda: len(model.seen) >= 2)
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    _, repl, _ = asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]), script))
    assert repl.run.store.get().tdd_paused is False
    texts = [str(m.content) for state in model.seen for m in state.messages if isinstance(m, HumanMessage)]
    assert development_continue_prompt(repl.plan) in texts


def test_resume_while_busy_only_unpauses(python_workspace, tmp_path):
    release = asyncio.Event()

    async def slow(state: Any) -> list[Any]:
        await release.wait()
        return [AIMessage(content="stop")]

    model = Model([slow])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.busy)
        repl.run.ctx.set_app_state(lambda s: dataclasses.replace(s, tdd_paused=True))
        await repl.run_command(SlashCommand("resume"))
        assert io.out[-1] == "Resumed."
        release.set()
        io.lines.put_nowait("/exit")

    _, repl, _ = asyncio.run(session(python_workspace, tmp_path, model,
                                     TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    assert repl.run.store.get().tdd_paused is False


def test_requirements_replans_and_continues(python_workspace, tmp_path):
    asked: list[str] = []

    async def rescope(request: str) -> tuple[str, list[str]] | None:
        asked.append(request)
        return ("## 5. Core Dependencies\n- FastAPI\n", ["X", "Y"]) if request == "add X" else None

    model = Model([[AIMessage(content="a")], [AIMessage(content="b")], [AIMessage(content="c")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: not repl.busy and repl.last_terminal is not None)
        io.lines.put_nowait("/requirements")
        await until(lambda: "Usage: /requirements <what to change>" in io.out)
        io.lines.put_nowait("/requirements nothing")
        await until(lambda: "The requirements were not changed." in io.out)
        io.lines.put_nowait("/requirements add X")
        await until(lambda: len(model.seen) >= 2)
        await until(lambda: not repl.busy)
        io.lines.put_nowait("/exit")

    done = TddPlan.from_texts(["A", "B"]).complete_current(CYCLE)
    _, repl, io = asyncio.run(session(python_workspace, tmp_path, model, done, script, rescope=rescope))
    assert asked == ["nothing", "add X"]
    assert [(i.text, i.status) for i in repl.plan.items] == [
        ("A", ItemStatus.DONE), ("X", ItemStatus.PENDING), ("Y", ItemStatus.PENDING)]
    assert repl.specification == "## 5. Core Dependencies\n- FastAPI\n"
    assert repl.run.store.get().declared_dependencies == frozenset({"FastAPI"})
    assert "New plan:\n✔ 1. A\n▶ 2. X\n· 3. Y" in io.out
    texts = [str(m.content) for state in model.seen for m in state.messages if isinstance(m, HumanMessage)]
    assert development_replanned_prompt(repl.specification, repl.plan) in texts


def test_requirements_without_a_rescope_is_unavailable(python_workspace, tmp_path):
    model = Model([[AIMessage(content="a")]])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: not repl.busy and repl.last_terminal is not None)
        io.lines.put_nowait("/requirements x")
        await until(lambda: "/requirements is not available in this session." in io.out)
        io.lines.put_nowait(None)

    terminal, _, _ = asyncio.run(session(python_workspace, tmp_path, model,
                                         TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    assert terminal == Terminal.COMPLETED


def test_exit_while_busy_interrupts_first(python_workspace, tmp_path):
    started = asyncio.Event()

    async def runs_until_cancelled(state: Any) -> list[Any]:
        started.set()
        while not state.tool_context.cancel.cancelled:
            await asyncio.sleep(0.01)
        return [AIMessage(content="cut off")]

    model = Model([runs_until_cancelled])

    async def script(repl: Repl, io: FakeIO) -> None:
        await started.wait()
        io.lines.put_nowait("/quit")

    _, repl, _ = asyncio.run(session(python_workspace, tmp_path, model,
                                     TddPlan.from_texts(["A"]).complete_current(CYCLE), script))
    assert repl.exiting and not repl.busy


def test_a_crashed_turn_is_reported_and_the_session_goes_on(python_workspace, tmp_path, monkeypatch):
    import app.screens.repl as repl_mod

    async def broken(run: Any, *a: Any) -> Terminal:
        raise RuntimeError("boom")

    monkeypatch.setattr(repl_mod, "begin_session", broken)
    model = Model([])

    async def script(repl: Repl, io: FakeIO) -> None:
        await until(lambda: repl.last_terminal is not None)
        io.lines.put_nowait("/exit")

    terminal, _, io = asyncio.run(session(python_workspace, tmp_path, model, TddPlan.from_texts(["A"]), script))
    assert terminal == Terminal.MODEL_ERROR
    assert any(line.startswith("Stopped (model_error)") for line in io.out)


# ── develop_interactive ──────────────────────────────────────────────────────

def test_develop_interactive_runs_the_repl_and_rescopes_through_the_shell(python_workspace, tmp_path):
    from app.session.driver import (
        MEMORY_CHECKPOINT,
        SessionOptions,
        build_session_shell,
        develop_interactive,
        drive_session,
    )

    def checklist(user_input: str, history: str) -> dict[str, Any]:
        return {"response": "Checklist. Proceed?", "needs_clarification": False, "has_checklist": True}

    def planner(spec: str, completed: Any) -> list[str]:
        return ["more"] if completed else ["first"]

    options = SessionOptions(checkpoint=MEMORY_CHECKPOINT, permission_mode="acceptEdits")
    shell, _ = build_session_shell("t-i", options, workspace_factory=lambda t: python_workspace,
                                   analyzer_fn=checklist, engineer_fn=lambda r, h: "SPEC", planner_fn=planner)
    run = drive_session(shell, "t-i", "req", lambda p: "/yes")
    model = Model([*cycle_turns(), [AIMessage(content="ok")]])
    io = FakeIO()

    async def typist() -> None:
        loop_io = io
        while "Every sub-requirement is done" not in loop_io.text():
            await asyncio.sleep(0.01)
        loop_io.lines.put_nowait("/requirements add more")
        while not any("Requirements Analyst" in line for line in loop_io.out):
            await asyncio.sleep(0.01)
        loop_io.lines.put_nowait("/yes")
        while not any(line.startswith("New plan:") for line in loop_io.out):
            await asyncio.sleep(0.01)
        loop_io.lines.put_nowait("/exit")

    def factory(on_interrupt: Any) -> FakeIO:
        asyncio.get_running_loop().create_task(typist())
        return io

    outcome = develop_interactive(shell, "t-i", run.values, python_workspace, options, io_factory=factory,
                                  deps=deps_for(model), project_root=tmp_path, home=tmp_path / "home",
                                  managed_dir=tmp_path / "etc")
    assert [(i.text, i.status) for i in outcome.plan.items] == [
        ("first", ItemStatus.DONE), ("more", ItemStatus.PENDING)]
    assert shell.get_state("t-i").values["plan"] == ["more"]
    assert outcome.completed is False
