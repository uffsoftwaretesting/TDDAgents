"""
TDD Stop Hook: tdd_phase_incomplete (Part D5).

Refuses turn exit when the Red->Green cycle is incomplete while remaining terminable
via the `stop_hook_active` contract (§3.3 of `docs/transition_elaboration_plan.md`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from app.hooks.dispatcher import HookOutcome
from app.loop.deps import StopHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.tdd import (
    GENERIC_WRITER_TOOL_NAMES,
    IMPLEMENTATION_WRITER_TOOL_NAMES,
    TEST_WRITER_TOOL_NAMES,
    is_implementation_writing_tool,
    is_test_path,
    is_test_writing_tool,
)

if TYPE_CHECKING:
    from app.loop.config import RunConfig
    from app.loop.state import LoopState


async def tdd_phase_incomplete_hook(state: LoopState, config: RunConfig) -> StopHookResult:
    """
    Stop hook that blocks turn completion if the TDD cycle is incomplete (Part D5).

    The stop_hook_active contract:
    - If state.stop_hook_active is True: the hook already blocked once on this context.
      To prevent infinite loops, it declines to return blocking errors again, setting
      prevent_continuation=True to terminate with STOP_HOOK_PREVENTED rather than COMPLETED.
    - If state.stop_hook_active is not True:
      - If ledger.is_cycle_complete: cycle complete, allows termination (COMPLETED).
      - If cycle incomplete: returns blocking_errors explaining why the model cannot stop.
    """
    ledger = state.tool_context.get_app_state().phase_ledger

    if ledger.is_cycle_complete:
        return StopHookResult()

    # Cycle is incomplete
    if state.stop_hook_active:
        # Honour stop_hook_active: prevent continuation to stop the turn without retrying forever
        return StopHookResult(prevent_continuation=True)

    # First block: build explanation of why turn cannot exit
    if not ledger.red_confirmed:
        if ledger.green_passed:
            # F2 "green in red" case
            msg = (
                "TDD cycle incomplete: tests passed on first run without prior failure "
                "(red_confirmed=False). A failing test must be observed (RED phase) before implementation."
            )
        else:
            msg = (
                "TDD cycle incomplete: no failing test observed yet in RED phase "
                "(red_confirmed=False). You must write a failing test and run RunTests."
            )
    else:
        msg = (
            "TDD cycle incomplete: tests are not passing yet in GREEN phase "
            "(green_passed=False). You must implement code and run RunTests until tests pass."
        )

    return StopHookResult(blocking_errors=(HumanMessage(content=msg),))


def tdd_pre_tool_use_hook(
    tool_name: str,
    tool_input: dict[str, Any],
    ledger: PhaseLedger,
    tool: Any = None,
) -> HookOutcome:
    """
    PreToolUse hook that enforces TDD phase boundaries (Plan B1).
    - In RED: implementation writing is blocked; test writing is allowed.
    - In GREEN/REFACTOR: test writing is blocked; implementation writing is allowed.
    - RunTests is always allowed.
    """
    if tool_name == "RunTests":
        return HookOutcome(denied=False)

    if ledger.phase == TddPhase.RED:
        if tool_name in IMPLEMENTATION_WRITER_TOOL_NAMES or (tool and is_implementation_writing_tool(tool)):
            return HookOutcome(
                denied=True,
                reason=(
                    f"TDD phase RED denies implementation-writing tool '{tool_name}' "
                    "before a failing test is confirmed."
                ),
            )
        if tool_name in GENERIC_WRITER_TOOL_NAMES or tool_name in ("WriteFile", "Edit", "MultiEdit"):
            path = str(tool_input.get("path") or tool_input.get("file_path") or "")
            if path and not is_test_path(path):
                return HookOutcome(
                    denied=True,
                    reason=(
                        f"TDD phase RED denies writing to production file '{path}'. "
                        "Only test files may be written in RED phase."
                    ),
                )

    elif ledger.phase in (TddPhase.GREEN, TddPhase.REFACTOR):
        if tool_name in TEST_WRITER_TOOL_NAMES or (tool and is_test_writing_tool(tool)):
            return HookOutcome(
                denied=True,
                reason=f"TDD phase {ledger.phase} denies test-writing tool '{tool_name}'.",
            )
        if tool_name in GENERIC_WRITER_TOOL_NAMES or tool_name in ("WriteFile", "Edit", "MultiEdit"):
            path = str(tool_input.get("path") or tool_input.get("file_path") or "")
            if path and is_test_path(path):
                return HookOutcome(
                    denied=True,
                    reason=f"TDD phase {ledger.phase} denies modifying test file '{path}'.",
                )

    return HookOutcome(denied=False)


def tdd_post_tool_use_hook(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_response: str,
    ledger: PhaseLedger,
) -> HookOutcome:
    """
    PostToolUse hook that observes test executions and provides TDD ledger feedback (Plan B1).
    """
    if tool_name == "RunTests":
        content = tool_response.lower()
        if "fail" in content or "error" in content:
            if ledger.phase == TddPhase.RED:
                return HookOutcome(
                    additional_context="TDD RED phase test failure observed. Ready to transition to GREEN phase."
                )
        elif "pass" in content or "ok" in content:
            if ledger.phase in (TddPhase.GREEN, TddPhase.REFACTOR):
                return HookOutcome(
                    additional_context=(
                        "TDD GREEN phase test pass observed. Ready to transition to REFACTOR or complete cycle."
                    )
                )

    return HookOutcome()
