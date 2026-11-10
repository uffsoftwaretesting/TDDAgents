"""
TDD phase-derived permission rules and tool classification (Parts D3 & D4).

Phase deny rules (§3.3 of `docs/transition_elaboration_plan.md`; Phase 12 cycle):
- RED: denies implementation writers (the tests come first: only test files may be written)
- GREEN: denies test writers (the model cannot edit the tests to make them pass)
- REFACTOR: denies nothing; it is mandatory and improves both the code and the tests
  (user decision, 2026-10-28), while every RunTests run must stay green
- RunTests is NEVER denied.

Which files are tests is the project's declaration (2026-11-03, `app/tdd/project_config.py`): the
`testFiles` and `testSupportFiles` gitignore patterns of `.tddagents/project.json`, approved with the
plan. Declared manifests and lockfiles (`manifestFiles`) are writable in every phase, beside agent
memory and the session's plan file. With no declaration the tier fails closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.project_config import PROJECT_CONFIG_FILE, TestFileRules, project_relative_path
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


#: What the tier says when the project declares no configuration (2026-11-03): it cannot tell a test
#: file from production code, so it fails closed while a sub-requirement is current.
NO_PROJECT_CONFIG = (
    "the project declares no test command, so its test files cannot be told from its production code. "
    "The project's configuration is declared in the plan's \"## Project Configuration\" section and approved "
    "with the plan (ExitPlanMode)."
)


def classify_path(path: str, rules: TestFileRules, root: str | None) -> str:
    """
    `"manifest"`, `"test"` or `"production"`, by the project's declared patterns
    (`app/tdd/project_config.py`). A path outside the project is production: it is neither a test
    file nor a manifest the project declares.
    """
    relative = project_relative_path(path, root)
    if relative is None:
        return "production"
    if rules.is_manifest(relative):
        return "manifest"
    return "test" if rules.is_test_code(relative) else "production"


def get_phase_deny_rules(ledger: PhaseLedger) -> tuple[PermissionRule, ...]:
    """
    The pool's phase deny rules (§3.3 and D3/D4): the dedicated implementation writers in RED,
    the dedicated test writers in GREEN. A generic file tool is never removed from the pool; its
    target path is judged at the gate (`check_tdd_phase_permission`). RunTests is never included.
    """
    if ledger.phase == TddPhase.RED:
        names = IMPLEMENTATION_WRITER_TOOL_NAMES
    elif ledger.phase == TddPhase.GREEN:
        names = TEST_WRITER_TOOL_NAMES
    else:
        return ()
    return tuple(PermissionRule(tool_name=name, rule_behavior=PermissionBehavior.DENY,
                                source=PermissionRuleSource.POLICY_SETTINGS) for name in sorted(names))


def check_tdd_phase_permission(
    tool: Tool,
    input_args: dict[str, Any],
    ledger: PhaseLedger,
    *,
    rules: TestFileRules | None,
    root: str | None = None,
    agent_memory: bool = False,
    plan_file: bool = False,
    cwd_offset: str | None = None,
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

    Which paths are tests is the project's declaration (`rules`, from `.tddagents/project.json`,
    2026-11-03), matched relative to the project `root`. A declared manifest or lockfile is writable in
    every phase, as any file is upstream. With no declaration (`rules` is `None`) every write the tier
    judges is refused (`NO_PROJECT_CONFIG`): the tier fails closed.
    """
    if tool.name == "RunTests" or ((agent_memory or plan_file) and tool.name in GENERIC_WRITER_TOOL_NAMES):
        return True, ""

    if tool.name == BASH_TOOL_NAME:
        from app.tdd.bash_phase_permissions import check_bash_tdd_phase

        return check_bash_tdd_phase(str(input_args.get("command") or ""), ledger, cwd_offset, rules=rules, root=root)

    raw_path = input_args.get("path") or input_args.get("file_path")
    if raw_path and tool.name in GENERIC_WRITER_TOOL_NAMES \
            and project_relative_path(str(raw_path), root) == PROJECT_CONFIG_FILE:
        # The project's declared configuration is judged by no phase (user decision 2026-11-10), as a
        # manifest: a broken declaration must be fixable in RED and with no valid declaration at all.
        # The protected folder still asks the user, the Edit check refuses an invalid result, and the
        # session reads an approved edit at once.
        return True, ""
    if rules is None:
        if (raw_path and tool.name in GENERIC_WRITER_TOOL_NAMES) or is_implementation_writing_tool(tool) \
                or is_test_writing_tool(tool):
            return False, f"TDD phase {ledger.phase} denies '{tool.name}': {NO_PROJECT_CONFIG}"
        return True, ""
    kind = classify_path(str(raw_path), rules, root) if raw_path else None
    if kind == "manifest" and tool.name in GENERIC_WRITER_TOOL_NAMES:
        return True, ""

    if ledger.phase == TddPhase.RED:
        if is_implementation_writing_tool(tool):
            return (
                False,
                (f"TDD phase RED denies implementation-writing tool '{tool.name}': "
                 "write and run this sub-requirement's tests first."),
            )
        if tool.name in GENERIC_WRITER_TOOL_NAMES:
            if raw_path:
                path = str(raw_path)
                if kind != "test":
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
            if raw_path:
                path = str(raw_path)
                if kind == "test":
                    return (
                        False,
                        (f"TDD phase {ledger.phase} denies modifying test file '{path}': GREEN implements "
                         f"what the tests describe. Tests can be improved in REFACTOR. {BACK_TO_RED}"),
                    )

    return True, ""
