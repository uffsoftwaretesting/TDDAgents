"""
Tests for TDD phase-derived deny rules in pool assembly (Part D3) and runtime gate (Part D4).
"""

from __future__ import annotations

import asyncio

from collections.abc import Callable
from typing import Any

from app.loop.context import AppState, AppStateStore, tool_context_for


from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.tdd import (
    GENERIC_WRITER_TOOL_NAMES,
    IMPLEMENTATION_WRITER_TOOL_NAMES,
    TEST_WRITER_TOOL_NAMES,
    check_tdd_phase_permission,
    get_phase_deny_rules,
    is_implementation_writing_tool,
    is_test_path,
    is_test_writing_tool,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.loop.tools.base import BuiltTool, Tool, build_tool
from app.loop.tools.pool import assemble_tool_pool
from app.loop.tools.run_tests import RunTests
from app.loop.tools.types import ToolResult


def make_dummy_tool(
    name: str,
    *,
    is_impl: bool | Callable[[], bool] = False,
    is_test: bool | Callable[[], bool] = False,
) -> BuiltTool:
    return build_tool(
        name=name,
        prompt=f"Tool {name}",
        call=lambda args, ctx: ToolResult(content=f"called {name}"),
        is_implementation_writer=is_impl,
        is_test_writer=is_test,
    )


def test_is_test_path():

    # Standard posix paths
    assert is_test_path("tests/test_engine.py") is True
    assert is_test_path("test/test_core.py") is True
    assert is_test_path("app/tests/sub/test_foo.py") is True
    assert is_test_path("test_something.py") is True
    assert is_test_path("something_test.py") is True

    # Windows paths with backslashes
    assert is_test_path("tests\\test_engine.py") is True
    assert is_test_path("test\\test_core.py") is True
    assert is_test_path("tests\\fixtures\\sample.json") is True
    assert is_test_path("test\\helpers\\util.py") is True
    assert is_test_path("src\\sub\\my_test.py") is True
    assert is_test_path("src\\sub\\test_my.py") is True

    # Segments in directory vs filename
    assert is_test_path("tests/helpers/util.py") is True
    assert is_test_path("test/helpers/util.py") is True
    assert is_test_path("src/components/foo_test.py") is True
    assert is_test_path("src/components/test_foo.py") is True

    # Case insensitivity
    assert is_test_path("TESTS/test_engine.py") is True
    assert is_test_path("Test/Core.py") is True
    assert is_test_path("src/TEST_FOO.py") is True
    assert is_test_path("src/FOO_TEST.PY") is True

    # Non-test paths
    assert is_test_path("src/engine.py") is False
    assert is_test_path("app/loop/engine.py") is False
    assert is_test_path("main.py") is False
    assert is_test_path("") is False
    assert is_test_path("   ") is False
    assert is_test_path("tests_extended/util.py") is False
    assert is_test_path("src/sub/mytests/foo.py") is False
    assert is_test_path("src/sub/tests/foo.py") is True
    assert is_test_path("src/sub/test/foo.py") is True


def test_tool_classification_helpers():
    # Implementation writer tools
    for name in IMPLEMENTATION_WRITER_TOOL_NAMES:
        t = make_dummy_tool(name)
        assert is_implementation_writing_tool(t) is True

    custom_impl = make_dummy_tool("CustomTool", is_impl=True)
    assert is_implementation_writing_tool(custom_impl) is True

    custom_not_impl = make_dummy_tool("CustomTool", is_impl=False)
    assert is_implementation_writing_tool(custom_not_impl) is False

    def boom() -> bool:
        raise RuntimeError("boom")

    broken_impl = make_dummy_tool("BrokenImpl", is_impl=boom)
    assert is_implementation_writing_tool(broken_impl) is False

    broken_test = make_dummy_tool("BrokenTest", is_test=boom)
    assert is_test_writing_tool(broken_test) is False

    class RaisingTool:
        name: str = "RaisingTool"
        prompt: str = ""
        input_schema: dict[str, Any] = {}
        aliases: tuple[str, ...] = ()
        is_mcp: bool = False

        def description(self, input: dict[str, Any]) -> str:
            return ""

        def is_enabled(self) -> bool:
            return True

        def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
            return False

        def is_read_only(self, input: dict[str, Any]) -> bool:
            return False

        def is_destructive(self, input: dict[str, Any]) -> bool:
            return False

        def is_implementation_writer(self) -> bool:
            raise RuntimeError("boom")

        def is_test_writer(self) -> bool:
            raise RuntimeError("boom")

        def validate_input(self, input: dict[str, Any], context: Any) -> Any:
            return None

        def check_permissions(self, input: dict[str, Any], context: Any) -> Any:
            return None

        async def call(self, input: dict[str, Any], context: Any) -> Any:
            return None

        def map_result(self, result: Any, tool_use_id: str) -> Any:
            return None

    raising = RaisingTool()
    assert is_implementation_writing_tool(raising) is False
    assert is_test_writing_tool(raising) is False

    class NoAttrTool:
        name: str = "NoAttrTool"
        prompt: str = ""
        input_schema: dict[str, Any] = {}
        aliases: tuple[str, ...] = ()
        is_mcp: bool = False

        def description(self, input: dict[str, Any]) -> str:
            return ""

        def is_enabled(self) -> bool:
            return True

        def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
            return False

        def is_read_only(self, input: dict[str, Any]) -> bool:
            return False

        def is_destructive(self, input: dict[str, Any]) -> bool:
            return False

        def validate_input(self, input: dict[str, Any], context: Any) -> Any:
            return None

        def check_permissions(self, input: dict[str, Any], context: Any) -> Any:
            return None

        async def call(self, input: dict[str, Any], context: Any) -> Any:
            return None

        def map_result(self, result: Any, tool_use_id: str) -> Any:
            return None

    no_attr = NoAttrTool()
    assert is_implementation_writing_tool(no_attr) is False  # type: ignore[arg-type]
    assert is_test_writing_tool(no_attr) is False  # type: ignore[arg-type]


def test_get_phase_deny_rules_exact_generation():
    # RED phase rules
    red_ledger = PhaseLedger(phase=TddPhase.RED)

    red_rules = get_phase_deny_rules(red_ledger)

    expected_red_rules = []
    for name in sorted(IMPLEMENTATION_WRITER_TOOL_NAMES):
        expected_red_rules.append(
            PermissionRule(
                tool_name=name,
                rule_behavior=PermissionBehavior.DENY,
                rule_content=None,
                source=PermissionRuleSource.POLICY_SETTINGS,
            )
        )
    for name in sorted(GENERIC_WRITER_TOOL_NAMES):
        expected_red_rules.append(
            PermissionRule(
                tool_name=name,
                rule_behavior=PermissionBehavior.DENY,
                rule_content="src/*",
                source=PermissionRuleSource.POLICY_SETTINGS,
            )
        )
        expected_red_rules.append(
            PermissionRule(
                tool_name=name,
                rule_behavior=PermissionBehavior.DENY,
                rule_content="app/*",
                source=PermissionRuleSource.POLICY_SETTINGS,
            )
        )

    assert red_rules == tuple(expected_red_rules)
    assert "RunTests" not in {r.tool_name for r in red_rules}

    # GREEN phase rules
    green_ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    green_rules = get_phase_deny_rules(green_ledger)

    expected_green_rules = []
    for name in sorted(TEST_WRITER_TOOL_NAMES):
        expected_green_rules.append(
            PermissionRule(
                tool_name=name,
                rule_behavior=PermissionBehavior.DENY,
                rule_content=None,
                source=PermissionRuleSource.POLICY_SETTINGS,
            )
        )
    for name in sorted(GENERIC_WRITER_TOOL_NAMES):
        expected_green_rules.append(
            PermissionRule(
                tool_name=name,
                rule_behavior=PermissionBehavior.DENY,
                rule_content="tests/*",
                source=PermissionRuleSource.POLICY_SETTINGS,
            )
        )

    assert green_rules == tuple(expected_green_rules)
    assert "RunTests" not in {r.tool_name for r in green_rules}

    # REFACTOR phase rules
    refactor_ledger = PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True)
    refactor_rules = get_phase_deny_rules(refactor_ledger)
    assert refactor_rules == tuple(expected_green_rules)
    assert "RunTests" not in {r.tool_name for r in refactor_rules}


def test_check_tdd_phase_permission_comprehensive():
    red_ledger = PhaseLedger(phase=TddPhase.RED)
    green_ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
    refactor_ledger = PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True)

    # RunTests is always allowed in all phases (even if flagged as writer)
    custom_run_tests = build_tool(
        name="RunTests",
        prompt="run tests prompt",
        call=lambda args, ctx: ToolResult(content="ran"),
        is_implementation_writer=True,
        is_test_writer=True,
    )
    for ledger in (red_ledger, green_ledger, refactor_ledger):
        allowed, reason = check_tdd_phase_permission(RunTests, {}, ledger)
        assert allowed is True
        assert reason == ""

        allowed2, reason2 = check_tdd_phase_permission(custom_run_tests, {}, ledger)
        assert allowed2 is True
        assert reason2 == ""

    # In RED:

    # Dedicated implementation tool denied
    impl_tool = make_dummy_tool("WriteImplementation")
    allowed, reason = check_tdd_phase_permission(impl_tool, {}, red_ledger)
    assert allowed is False
    assert (
        reason
        == "TDD phase RED denies implementation-writing tool 'WriteImplementation' before a failing test is confirmed."
    )

    # Generic writer with path targeting production
    for gen_name in GENERIC_WRITER_TOOL_NAMES:
        gen_tool = make_dummy_tool(gen_name)
        allowed, reason = check_tdd_phase_permission(gen_tool, {"path": "src/core.py"}, red_ledger)
        assert allowed is False
        assert (
            reason
            == "TDD phase RED denies writing to production file 'src/core.py'. "
            "Only test files may be written in RED phase."
        )

        # with file_path argument
        allowed, reason = check_tdd_phase_permission(gen_tool, {"file_path": "app/core.py"}, red_ledger)
        assert allowed is False
        assert (
            reason
            == "TDD phase RED denies writing to production file 'app/core.py'. "
            "Only test files may be written in RED phase."
        )

        # with test path argument allowed in RED
        allowed, reason = check_tdd_phase_permission(gen_tool, {"path": "tests/test_core.py"}, red_ledger)
        assert allowed is True
        assert reason == ""

        # with empty path allowed
        allowed, reason = check_tdd_phase_permission(gen_tool, {}, red_ledger)
        assert allowed is True
        assert reason == ""

    # Non-writing tool allowed in RED
    read_tool = make_dummy_tool("ReadFile")
    allowed, reason = check_tdd_phase_permission(read_tool, {"path": "src/core.py"}, red_ledger)
    assert allowed is True
    assert reason == ""

    # In GREEN & REFACTOR:
    for g_ledger in (green_ledger, refactor_ledger):
        # Dedicated test tool denied
        test_tool = make_dummy_tool("WriteTest")
        allowed, reason = check_tdd_phase_permission(test_tool, {}, g_ledger)
        assert allowed is False
        assert reason == f"TDD phase {g_ledger.phase} denies test-writing tool 'WriteTest'."

        # Generic writer targeting test file denied
        for gen_name in GENERIC_WRITER_TOOL_NAMES:
            gen_tool = make_dummy_tool(gen_name)
            allowed, reason = check_tdd_phase_permission(gen_tool, {"path": "tests/test_core.py"}, g_ledger)
            assert allowed is False
            assert reason == f"TDD phase {g_ledger.phase} denies modifying test file 'tests/test_core.py'."

            # with file_path argument
            allowed, reason = check_tdd_phase_permission(gen_tool, {"file_path": "tests/test_core.py"}, g_ledger)
            assert allowed is False
            assert reason == f"TDD phase {g_ledger.phase} denies modifying test file 'tests/test_core.py'."

            # with production path allowed in GREEN
            allowed, reason = check_tdd_phase_permission(gen_tool, {"path": "src/core.py"}, g_ledger)
            assert allowed is True
            assert reason == ""

            # with empty path allowed
            allowed, reason = check_tdd_phase_permission(gen_tool, {}, g_ledger)
            assert allowed is True
            assert reason == ""

        # Non-writing tool allowed
        allowed, reason = check_tdd_phase_permission(read_tool, {"path": "tests/test_core.py"}, g_ledger)
        assert allowed is True
        assert reason == ""


class TestPoolAssemblyPhaseRules:

    """Part D3: assemble_tool_pool strips forbidden tools before model sees schema."""

    def test_red_phase_strips_implementation_writers(self):
        tools: list[Tool] = [
            make_dummy_tool("WriteImplementation"),
            make_dummy_tool("WriteCode"),
            make_dummy_tool("CustomCodeWriter", is_impl=True),
            make_dummy_tool("WriteTest"),
            make_dummy_tool("CustomTestWriter", is_test=True),
            make_dummy_tool("ReadFile"),
            RunTests,
        ]

        ledger = PhaseLedger(phase=TddPhase.RED)
        pool = assemble_tool_pool(tools, phase_ledger=ledger)
        pool_names = {t.name for t in pool}

        # Implementation writers must be absent
        assert "WriteImplementation" not in pool_names
        assert "WriteCode" not in pool_names
        assert "CustomCodeWriter" not in pool_names

        # Test writers and test runners must be present
        assert "WriteTest" in pool_names
        assert "CustomTestWriter" in pool_names
        assert "ReadFile" in pool_names
        assert "RunTests" in pool_names

    def test_green_phase_strips_test_writers(self):
        tools: list[Tool] = [
            make_dummy_tool("WriteImplementation"),
            make_dummy_tool("WriteTest"),
            make_dummy_tool("CustomTestWriter", is_test=True),
            make_dummy_tool("ReadFile"),
            RunTests,
        ]

        ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)
        pool = assemble_tool_pool(tools, phase_ledger=ledger)
        pool_names = {t.name for t in pool}

        assert "WriteTest" not in pool_names
        assert "CustomTestWriter" not in pool_names
        assert "WriteImplementation" in pool_names
        assert "ReadFile" in pool_names
        assert "RunTests" in pool_names

    def test_refactor_phase_strips_test_writers(self):
        tools: list[Tool] = [
            make_dummy_tool("WriteImplementation"),
            make_dummy_tool("WriteTest"),
            make_dummy_tool("ReadFile"),
            RunTests,
        ]

        ledger = PhaseLedger(phase=TddPhase.REFACTOR, red_confirmed=True, green_passed=True)
        pool = assemble_tool_pool(tools, phase_ledger=ledger)
        pool_names = {t.name for t in pool}

        assert "WriteTest" not in pool_names
        assert "WriteImplementation" in pool_names
        assert "ReadFile" in pool_names
        assert "RunTests" in pool_names


class TestRuntimePermissionGatePhaseRules:
    """Part D4: has_permissions_to_use_tool re-checks and denies forbidden tools at call time."""

    def test_red_phase_denies_implementation_writer_even_in_bypass_mode(self):
        async def go():
            store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(store, permission_context=perm_ctx)

            impl_tool = make_dummy_tool("WriteImplementation")
            res = await has_permissions_to_use_tool(impl_tool, {}, ctx)
            assert res.behavior == "deny"
            assert "RED denies implementation-writing tool" in res.message

        asyncio.run(go())

    def test_red_phase_denies_generic_writer_targeting_production_file(self):
        async def go():
            store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=TddPhase.RED)))
            ctx = tool_context_for(store)

            write_tool = make_dummy_tool("WriteFile")
            res = await has_permissions_to_use_tool(write_tool, {"path": "src/module.py"}, ctx)
            assert res.behavior == "deny"
            assert "RED denies writing to production file" in res.message

            # Allowed to write test files
            res_test = await has_permissions_to_use_tool(write_tool, {"path": "tests/test_module.py"}, ctx)
            assert res_test.behavior == "allow"

        asyncio.run(go())

    def test_green_phase_denies_test_writer_even_in_bypass_mode(self):
        async def go():
            store = AppStateStore(
                AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True))
            )
            perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
            ctx = tool_context_for(store, permission_context=perm_ctx)

            test_tool = make_dummy_tool("WriteTest")
            res = await has_permissions_to_use_tool(test_tool, {}, ctx)
            assert res.behavior == "deny"
            assert "GREEN denies test-writing tool" in res.message

        asyncio.run(go())

    def test_green_phase_denies_generic_writer_targeting_test_file(self):
        async def go():
            store = AppStateStore(
                AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True))
            )
            ctx = tool_context_for(store)

            write_tool = make_dummy_tool("WriteFile")
            res = await has_permissions_to_use_tool(write_tool, {"path": "tests/test_module.py"}, ctx)
            assert res.behavior == "deny"
            assert "GREEN denies modifying test file" in res.message

            # Allowed to write production files
            res_prod = await has_permissions_to_use_tool(write_tool, {"path": "src/module.py"}, ctx)
            assert res_prod.behavior == "allow"

        asyncio.run(go())

    def test_run_tests_is_never_denied_in_any_phase(self):
        async def go():
            for phase in (TddPhase.RED, TddPhase.GREEN, TddPhase.REFACTOR):
                store = AppStateStore(AppState(phase_ledger=PhaseLedger(phase=phase)))
                ctx = tool_context_for(store)
                res = await has_permissions_to_use_tool(RunTests, {}, ctx)
                assert res.behavior == "allow"

        asyncio.run(go())
