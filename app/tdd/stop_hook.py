"""
TDD Stop Hook: tdd_phase_incomplete (Part D5).

Refuses turn exit when the Red -> Green -> Refactor cycle is incomplete while remaining terminable
via the `stop_hook_active` contract (§3.3 of `docs/transition_elaboration_plan.md`).

**This module holds the Stop hook and nothing else.** Phase 2 deleted the PreToolUse and
PostToolUse hooks that used to live here:

* the PreToolUse one duplicated `tdd/phase_permissions.py::check_tdd_phase_permission`, giving
  RED two enforcement points and so two places to get it wrong. The gate is the surviving
  one, because a PreToolUse hook's `allow` cannot talk its way past it
  (`services/tools/tool_hooks.py`);
* the PostToolUse one decided the phase by grepping tool output for the words "fail" and
  "error", which could contradict the ledger it was describing — output mentioning an error
  while the suite passes is ordinary. `RunTests` and its exit code are the only evidence the
  phase moves on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from app.query.stop_hooks import StopHookResult
from app.tdd.coverage_gate import COVERAGE_RULE_OF_THUMB
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import BACK_TO_RED
from app.tdd.tdd_plan_tool.tdd_plan_tool import UPDATE_PLAN_TOOL_NAME
from app.tools.ask_user_question_tool.ask_user_question_tool import (
    ASK_USER_QUESTION_TOOL_NAME,
)

if TYPE_CHECKING:
    from app.query.config import RunConfig
    from app.query.state import LoopState


#: Upstream's own escalation rule (`constants/prompts.ts`, `getSimpleDoingTasksSection`), named in
#: every refusal when the pool holds `AskUserQuestion` (user decision 2026-10-28). It replaced
#: TDDAgents' "If you need a decision from the user, call AskUserQuestion…", which invited a
#: question at every refusal: a live CPF run asked the same thing three times and never acted.
ASK_THE_USER = ("Escalate to the user with AskUserQuestion only when you're genuinely stuck after investigation, "
                "not as a first response to friction.")

#: RED's two ways forward (user decision 2026-10-28). A plan can hold a sub-requirement whose
#: behaviour an earlier one already built; RED still needs tests new or changed for it.
RED_WAYS_FORWARD = ("Write or extend the tests for this sub-requirement, covering every relevant scenario and edge "
                    "case, then run RunTests.")
RED_DUPLICATE_WAY = ("If an earlier sub-requirement already built and tested this one's behaviour, you may instead "
                     "propose an UpdatePlan change that merges or rewords it; the user approves it.")


def _red_fact(state: LoopState) -> str:
    """Whether the test files changed since the sub-requirement started, as RunTests observes it."""
    from app.tdd.suite_fingerprint import fingerprint_test_files, have_tests_changed

    app_state = state.tool_context.get_app_state()
    current = fingerprint_test_files(getattr(state.tool_context, "workspace", None))
    if current is None:
        return "they could not be read"
    if not have_tests_changed(current, app_state.tdd_item_test_baseline):
        return "they have not changed since this sub-requirement started"
    return "they changed, and no RunTests run has counted since"


def _last_run_fact(state: LoopState) -> str:
    """The last RunTests run, as recorded: its exit code and pytest's meaning, or none."""
    from app.tdd.run_tests_tool.run_tests_tool import exit_semantic

    run = state.tool_context.get_app_state().last_test_run
    if run is None:
        return "no RunTests run has been recorded for it yet"
    return f"the last RunTests run exited {run.exit_code} ({exit_semantic(run.exit_code).meaning})"


def incomplete_cycle_message(ledger: PhaseLedger, state: LoopState) -> str:
    """
    The phase, its rule (RunTests' `PHASE_RULES`, one source) and the observed fact, then what is
    open (2026-10-28): the refusal states what the ledger needs, as RunTests does, so a model cannot
    take tests written for an earlier sub-requirement as this one's.
    """
    from app.tdd.run_tests_tool.run_tests_tool import PHASE_RULES

    tools = {getattr(tool, "name", None) for tool in state.tool_context.tools}
    if not ledger.tests_written:
        open_work = RED_WAYS_FORWARD
        if UPDATE_PLAN_TOOL_NAME in tools:
            open_work = f"{open_work} {RED_DUPLICATE_WAY}"
        return f"TDD cycle incomplete: RED. {PHASE_RULES[TddPhase.RED]}: {_red_fact(state)}. {open_work}"
    if not ledger.green_passed:
        return (f"TDD cycle incomplete: GREEN. {PHASE_RULES[TddPhase.GREEN]}: {_last_run_fact(state)}. Implement "
                f"the code the tests describe and run RunTests until every test passes. {BACK_TO_RED}")
    figure = "not measured yet" if ledger.coverage is None else f"{ledger.coverage:.1f}%"
    return (f"TDD cycle incomplete: REFACTOR. {PHASE_RULES[TddPhase.REFACTOR]}: {_last_run_fact(state)}. It is "
            "mandatory: improve the code and the tests and raise line + branch coverage (a good rule of thumb is "
            f"{COVERAGE_RULE_OF_THUMB:.0f}%; last measured: {figure}), then run RunTests until every test passes.")


async def tdd_phase_incomplete_hook(state: LoopState, config: RunConfig) -> StopHookResult:
    """
    Stop hook that blocks turn completion if the TDD cycle is incomplete (Part D5).

    It never gives up (Phase 12, user decision 2026-10-28, as upstream's Stop hooks, which may
    block every stop): while the cycle is incomplete, every stop is blocked with what is still
    missing, naming the current sub-requirement when there is a plan. `stop_hook_active` is
    no latch here; a turn ends early only on `/pause`, a configured hook's `continue: false`,
    an abort or an error. A complete cycle allows termination (COMPLETED). The message states the
    phase's rule and what was observed (`incomplete_cycle_message`); when the run's pool holds
    `AskUserQuestion`, it ends with upstream's own escalation rule (`ASK_THE_USER`).
    """
    app_state = state.tool_context.get_app_state()
    ledger = app_state.phase_ledger

    if ledger.is_cycle_complete:
        return StopHookResult()

    current = app_state.tdd_plan.current
    msg = incomplete_cycle_message(ledger, state)
    if current is not None:
        msg = f"Current sub-requirement {current.id}: {current.text}\n\n{msg}"

    if any(getattr(tool, "name", None) == ASK_USER_QUESTION_TOOL_NAME for tool in state.tool_context.tools):
        msg = f"{msg} {ASK_THE_USER}"
    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))
