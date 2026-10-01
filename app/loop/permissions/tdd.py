"""
TDD phase-derived permission rules and tool classification (Parts D3 & D4).

Phase deny rules (§3.3 of `docs/transition_elaboration_plan.md`):
- RED: denies implementation writers (model cannot write production code before a failing test exists)
- GREEN: denies test writers (model cannot edit the test to make it pass)
- REFACTOR: denies test writers (behaviour is pinned while structure changes)
- RunTests is NEVER denied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
)

if TYPE_CHECKING:
    from app.loop.tools.base import Tool

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
        except Exception:
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
        except Exception:
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
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return True
    return False


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
    elif ledger.phase in (TddPhase.GREEN, TddPhase.REFACTOR):
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
) -> tuple[bool, str]:
    """
    Check if a tool call is permitted by the current TDD phase.
    Returns (permitted: bool, reason: str).
    RunTests is always permitted.
    """
    if tool.name == "RunTests":
        return True, ""

    if ledger.phase == TddPhase.RED:
        if is_implementation_writing_tool(tool):
            return (
                False,
                f"TDD phase RED denies implementation-writing tool '{tool.name}' "
                "before a failing test is confirmed.",
            )
        if tool.name in GENERIC_WRITER_TOOL_NAMES:
            raw_path = input_args.get("path") or input_args.get("file_path")
            if raw_path:
                path = str(raw_path)
                if not is_test_path(path):
                    return (
                        False,
                        f"TDD phase RED denies writing to production file '{path}'. "
                        "Only test files may be written in RED phase.",
                    )
    elif ledger.phase in (TddPhase.GREEN, TddPhase.REFACTOR):
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
                        f"TDD phase {ledger.phase} denies modifying test file '{path}'.",
                    )

    return True, ""
