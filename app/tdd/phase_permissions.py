"""
TDD phase-derived permission rules and tool classification (Parts D3 & D4).

Phase deny rules (§3.3 of `docs/transition_elaboration_plan.md`; Phase 12 cycle):
- RED: denies implementation writers (the tests come first: only test files may be written)
- GREEN: denies test writers (the model cannot edit the tests to make them pass)
- REFACTOR: denies nothing; it is mandatory and improves both the code and the tests
  (user decision, 2026-10-28), while every RunTests run must stay green
- RunTests is NEVER denied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.tdd.ledger import PhaseLedger, TddPhase
from app.types.permissions import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
)

if TYPE_CHECKING:
    from app.tool import Tool

#: How a wrong test is fixed while the tests are locked (Phase 12): go back to RED by spawning
#: the tester, whose phase-on-spawn restarts the item (`PhaseLedger.transition_to`).
BACK_TO_RED = (
    "If a test is wrong (it contradicts the specification), go back to RED: spawn the tester agent "
    "(Agent with subagent_type 'tester') to fix it. That restarts this sub-requirement at RED; then "
    "implement again."
)

#: The shell tool, whose write targets live inside its command string.
BASH_TOOL_NAME = "Bash"

#: Dedicated tools for writing production code
IMPLEMENTATION_WRITER_TOOL_NAMES: frozenset[str] = frozenset({
    "WriteImplementation",
    "WriteCode",
    "EditImplementation",
    "EditCode",
})

#: Dedicated tools for writing test code
TEST_WRITER_TOOL_NAMES: frozenset[str] = frozenset({
    "WriteTest",
    "EditTest",
})

#: Generic file-writing tools that take a target path
GENERIC_WRITER_TOOL_NAMES: frozenset[str] = frozenset({
    "WriteFile",
    "Edit",
    "MultiEdit",
})


def is_implementation_writing_tool(tool: Tool) -> bool:
    """
    Check if a tool is designated as an implementation writer.
    Matches by known name or tool.is_implementation_writer() hook.
    """
    if tool.name in IMPLEMENTATION_WRITER_TOOL_NAMES:
        return True
    fn = getattr(tool, "is_implementation_writer", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - a tool's own classifier hook must not crash the gate
            return False
    return False


def is_test_writing_tool(tool: Tool) -> bool:
    """
    Check if a tool is designated as a test writer.
    Matches by known name or tool.is_test_writer() hook.
    """
    if tool.name in TEST_WRITER_TOOL_NAMES:
        return True
    fn = getattr(tool, "is_test_writer", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - a tool's own classifier hook must not crash the gate
            return False
    return False


def is_test_path(path: str) -> bool:
    """
    Determine if a file path targets test code rather than production code.
    Matches directory segments (e.g. tests/, test/) or filenames (test_*.py, *_test.py).
    """
    normalized = path.replace("\\", "/").strip().lower()
    if not normalized:
        return False
    parts = normalized.split("/")
    if any(p in ("tests", "test") for p in parts[:-1]):
        return True
    basename = parts[-1]
    return basename.startswith("test_") or basename.endswith("_test.py")


def get_phase_deny_rules(ledger: PhaseLedger) -> tuple[PermissionRule, ...]:
    """
    Derive permission deny rules from the current TDD phase (§3.3 and D3/D4).
    RunTests is never included in deny rules.
    """
    rules: list[PermissionRule] = []

    if ledger.phase == TddPhase.RED:
        for name in sorted(IMPLEMENTATION_WRITER_TOOL_NAMES):
            rules.append(
                PermissionRule(
                    tool_name=name,
                    rule_behavior=PermissionBehavior.DENY,
                    source=PermissionRuleSource.POLICY_SETTINGS,
                )
            )
        for name in sorted(GENERIC_WRITER_TOOL_NAMES):
            rules.append(
                PermissionRule(
                    tool_name=name,
                    rule_behavior=PermissionBehavior.DENY,
                    rule_content="src/*",
                    source=PermissionRuleSource.POLICY_SETTINGS,
                )
            )
            rules.append(
                PermissionRule(
                    tool_name=name,
                    rule_behavior=PermissionBehavior.DENY,
                    rule_content="app/*",
                    source=PermissionRuleSource.POLICY_SETTINGS,
                )
            )
    elif ledger.phase == TddPhase.GREEN:
        for name in sorted(TEST_WRITER_TOOL_NAMES):
            rules.append(
                PermissionRule(
                    tool_name=name,
                    rule_behavior=PermissionBehavior.DENY,
                    source=PermissionRuleSource.POLICY_SETTINGS,
                )
            )
        for name in sorted(GENERIC_WRITER_TOOL_NAMES):
            rules.append(
                PermissionRule(
                    tool_name=name,
                    rule_behavior=PermissionBehavior.DENY,
                    rule_content="tests/*",
                    source=PermissionRuleSource.POLICY_SETTINGS,
                )
            )

    return tuple(rules)


def check_tdd_phase_permission(
    tool: Tool,
    input_args: dict[str, Any],
    ledger: PhaseLedger,
    *,
    agent_memory: bool = False,
    plan_file: bool = False,
) -> tuple[bool, str]:
    """
    Check if a tool call is permitted by the current TDD phase.
    Returns (permitted: bool, reason: str).
    RunTests is always permitted, and so is a file tool's write to this run's agent memory
    (`agent_memory`) or to the session's plan file (`plan_file`, plan mode): a note outside the
    project is neither a test nor production code.

    `Bash` is checked through its write targets rather than a path argument
    (`tdd/bash_phase_permissions.py`): without that, `echo code > calculator.py` would write production code
    in RED through a tool the path check cannot see, and barrier 2 would be bypassable by
    rephrasing the request.
    """
    if tool.name == "RunTests" or ((agent_memory or plan_file) and tool.name in GENERIC_WRITER_TOOL_NAMES):
        return True, ""

    if tool.name == BASH_TOOL_NAME:
        from app.tdd.bash_phase_permissions import check_bash_tdd_phase

        return check_bash_tdd_phase(str(input_args.get("command") or ""), ledger)

    if ledger.phase == TddPhase.RED:
        if is_implementation_writing_tool(tool):
            return (
                False,
                (f"TDD phase RED denies implementation-writing tool '{tool.name}': "
                 "write and run this sub-requirement's tests first."),
            )
        if tool.name in GENERIC_WRITER_TOOL_NAMES:
            raw_path = input_args.get("path") or input_args.get("file_path")
            if raw_path:
                path = str(raw_path)
                if not is_test_path(path):
                    return (
                        False,
                        (f"TDD phase RED denies writing to production file '{path}'. "
                         "Only test files may be written in RED phase."),
                    )
    elif ledger.phase == TddPhase.GREEN:
        if is_test_writing_tool(tool):
            return (
                False,
                f"TDD phase {ledger.phase} denies test-writing tool '{tool.name}'.",
            )
        if tool.name in GENERIC_WRITER_TOOL_NAMES:
            raw_path = input_args.get("path") or input_args.get("file_path")
            if raw_path:
                path = str(raw_path)
                if is_test_path(path):
                    return (
                        False,
                        (f"TDD phase {ledger.phase} denies modifying test file '{path}': GREEN implements "
                         f"what the tests describe. Tests can be improved in REFACTOR. {BACK_TO_RED}"),
                    )

    return True, ""
