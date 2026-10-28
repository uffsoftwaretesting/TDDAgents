"""
Phase 11: one development conversation over the whole plan (`app/cli/print.py`):
`open_session_run`, `begin_session`, `submit_prompt` and `run_headless`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.cli.print import (
    DevelopmentOutcome,
    begin_session,
    open_session_run,
    run_headless,
    session_run_id,
    submit_prompt,
)
from app.query.transitions import Terminal
from app.session.prompts import development_resume_prompt, development_start_prompt
from app.tdd.plan import ItemStatus, TddPlan, load_plan, plan_path, save_plan
from app.tdd.suite_fingerprint import fingerprint_test_files
from app.types.permissions import PermissionMode, ToolPermissionContext

CALC_TEST = "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n"
CALC_IMPL = "def add(a, b):\n    return a + b\n"
SUB_TEST = "import calculator\n\ndef test_sub():\n    assert calculator.sub(5, 3) == 2\n"
SUB_IMPL = CALC_IMPL + "\ndef sub(a, b):\n    return a - b\n"


def call(name: str, cid: str, **args: Any) -> AIMessage:
    return AIMessage(content=name, tool_calls=[{"name": name, "args": args, "id": cid}])


def scripted(turns: list[list[AIMessage]], seen: list[Any]) -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        seen.append(state)
        if not turns:
            raise AssertionError("model asked for more turns than scripted")
        for message in turns.pop(0):
            yield message

    return dataclasses.replace(get_production_deps(), call_model=model)


def cycle(test_file: str, test: str, impl: str, n: int) -> list[list[AIMessage]]:
    """RED (write and run the tests), GREEN (implement, pass), REFACTOR (pass with full coverage)."""
    return [
        [call("WriteFile", f"w{n}", file_path=test_file, content=test)],
        [call("RunTests", f"r{n}", test_path="tests")],
        [call("WriteFile", f"i{n}", file_path="calculator.py", content=impl)],
        [call("RunTests", f"g{n}", test_path="tests")],
        [call("RunTests", f"f{n}", test_path="tests")],  # REFACTOR: passing, fully covered
    ]


#: A plan whose only item is done: any turn may end, so chat turns can be scripted alone.
FINISHED = TddPlan.from_texts(["a"])
FINISHED = dataclasses.replace(FINISHED, items=(dataclasses.replace(FINISHED.items[0], status=ItemStatus.DONE),))

PERM = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)


def options(tmp_path: Path, deps: Any) -> dict[str, Any]:
    return {"deps": deps, "permission_context": PERM, "project_root": tmp_path, "home": tmp_path / "home",
            "managed_dir": tmp_path / "etc"}


def test_session_run_id_is_stable_per_session_and_fresh_without_one():
    assert session_run_id("t1") == session_run_id("t1") != session_run_id("t2")
    assert session_run_id("t1").startswith("loop-") and len(session_run_id("t1")) == len("loop-") + 32
    assert session_run_id(None) != session_run_id(None)


def test_headless_works_through_the_whole_plan_in_one_conversation(python_workspace, tmp_path):
    turns = [
        *cycle("tests/test_add.py", CALC_TEST, CALC_IMPL, 1), [AIMessage(content="add done")],
        *cycle("tests/test_sub.py", SUB_TEST, SUB_IMPL, 2), [AIMessage(content="sub done")],
    ]
    seen: list[Any] = []
    outcome = asyncio.run(run_headless("s1", "# Calculator", ["add", "sub"], workspace=python_workspace,
                                       **options(tmp_path, scripted(turns, seen))))
    assert outcome == DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=outcome.plan)
    assert outcome.completed is True
    assert [i.status for i in outcome.plan.items] == [ItemStatus.DONE, ItemStatus.DONE]
    first_prompt = seen[0].messages[0].content
    assert first_prompt == development_start_prompt("# Calculator", TddPlan.from_texts(["add", "sub"]))
    # The second item was announced inside the same conversation.
    later = [str(m.content) for m in seen[-1].messages if isinstance(m, HumanMessage)]
    assert any("Next sub-requirement (2 of 2): 2. sub" in text for text in later)
    saved = load_plan(plan_path(session_run_id("s1"), str(python_workspace.root)))
    assert saved == outcome.plan


def test_headless_stops_at_the_first_unfinished_item(python_workspace, tmp_path):
    turns = [[AIMessage(content="I am done")], [AIMessage(content="really done")]]
    outcome = asyncio.run(run_headless("s2", "spec", ["add", "sub"], workspace=python_workspace,
                                       **options(tmp_path, scripted(turns, []))))
    assert outcome.terminal == Terminal.STOP_HOOK_PREVENTED
    assert outcome.completed is False
    assert outcome.plan.done_count == 0


def test_a_completed_terminal_with_items_left_is_not_completed():
    outcome = DevelopmentOutcome(terminal=Terminal.COMPLETED, plan=TddPlan.from_texts(["a"]))
    assert outcome.completed is False


def test_a_later_turn_continues_the_same_conversation(python_workspace, tmp_path):
    turns = [[AIMessage(content="first")], [AIMessage(content="second")]]
    seen: list[Any] = []

    async def go() -> tuple[Terminal, Terminal]:
        async with open_session_run(run_id="loop-turns", plan=FINISHED, workspace=python_workspace,
                                    **options(tmp_path, scripted(turns, seen))) as run:
            first = await begin_session(run, "hello")
            second = await submit_prompt(run, "and now?")
            return first, second

    first, second = asyncio.run(go())
    assert (first, second) == (Terminal.COMPLETED, Terminal.COMPLETED)
    texts = [m.content for m in seen[1].messages if isinstance(m, (HumanMessage, AIMessage))]
    assert texts[0] == "hello" and "first" in texts and "and now?" in texts
    assert texts.index("first") < texts.index("and now?")


def test_a_resumed_session_keeps_its_saved_plan_and_restarts_the_ledger(python_workspace, tmp_path):
    run_id = session_run_id("s3")
    done_first = TddPlan.from_texts(["add", "sub"])
    done_first = dataclasses.replace(done_first, items=(
        dataclasses.replace(done_first.items[0], status=ItemStatus.DONE), done_first.items[1]))
    save_plan(plan_path(run_id, str(python_workspace.root)), done_first)
    python_workspace.write_file("tests/test_add.py", "def test_add():\n    pass\n")
    seen: list[Any] = []
    turns = [[AIMessage(content="stop")], [AIMessage(content="stop")]]

    async def go() -> Any:
        async with open_session_run(run_id=run_id, plan=TddPlan.from_texts(["add", "sub"]),
                                    workspace=python_workspace, **options(tmp_path, scripted(turns, seen))) as run:
            assert run.store.get().tdd_plan == done_first
            assert run.store.get().phase_ledger.tests_written is False
            # The resumed item's existing tests are its RED baseline: they must change again.
            assert run.store.get().tdd_item_test_baseline == fingerprint_test_files(python_workspace)
            assert run.ctx.tdd_plan_path == str(plan_path(run_id, str(python_workspace.root)))
            return await begin_session(run, "fresh", "resumed")

    assert asyncio.run(go()) == Terminal.STOP_HOOK_PREVENTED
    assert seen[0].messages[0].content == "fresh"


def test_a_fresh_session_saves_its_plan_at_once(python_workspace, tmp_path):
    async def go() -> None:
        async with open_session_run(run_id="loop-save", plan=TddPlan.from_texts(["a"]), workspace=python_workspace,
                                    **options(tmp_path, scripted([], []))):
            pass

    asyncio.run(go())
    path = plan_path("loop-save", str(python_workspace.root))
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["text"] == "a"


def test_with_a_transcript_the_conversation_is_resumed_with_the_resume_prompt(python_workspace, tmp_path):
    seen: list[Any] = []
    turns = [[AIMessage(content="one")], [AIMessage(content="two")]]

    async def session(prompt_fresh: str) -> None:
        async with open_session_run(run_id="loop-resume", plan=FINISHED, workspace=python_workspace,
                                    **options(tmp_path, scripted(turns, seen))) as run:
            await begin_session(run, prompt_fresh, development_resume_prompt(FINISHED))

    asyncio.run(session("first start"))
    asyncio.run(session("second start"))
    second = [m.content for m in seen[1].messages if isinstance(m, (HumanMessage, AIMessage))]
    assert "first start" in second and "one" in second
    assert "second start" not in second
    assert development_resume_prompt(FINISHED) in second


@pytest.mark.parametrize("plan", [TddPlan(), TddPlan.from_texts(["a"])])
def test_development_prompts_state_the_plan(plan: TddPlan):
    start = development_start_prompt("SPEC", plan)
    assert "## Technical specification\n\nSPEC" in start
    assert start.endswith("Start with sub-requirement 1: a." if plan.items
                          else "Start with sub-requirement (none: every sub-requirement is done).")
    assert development_start_prompt("  ", plan).count("(none)") >= 1
    resume = development_resume_prompt(plan)
    assert resume.startswith("The session was resumed.")


def test_an_interactive_run_can_prompt_and_holds_its_can_use_tool(python_workspace, tmp_path):
    async def can_use(*args: Any) -> Any:
        raise AssertionError("not asked here")

    seen: dict[str, Any] = {}

    async def go(handler: Any) -> None:
        opts = options(tmp_path, scripted([], []))
        opts.pop("permission_context")
        async with open_session_run(run_id=f"loop-i{bool(handler)}", plan=FINISHED, workspace=python_workspace,
                                    can_use_tool=handler, **opts) as run:
            seen[str(bool(handler))] = (run.ctx.can_use_tool,
                                        run.store.get().tool_permission_context.should_avoid_permission_prompts)

    asyncio.run(go(can_use))
    asyncio.run(go(None))
    assert seen == {"True": (can_use, False), "False": (None, True)}
