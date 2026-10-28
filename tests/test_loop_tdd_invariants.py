"""
Structural Invariant Property Tests for Part D (D6).

These two tests represent the thesis's structural claim in executable form
(§3.3 and §6.2 of `docs/transition_elaboration_plan.md`):

1. No reachable tool pool in RED contains an implementation-writing tool,
   for any ledger state the loop can produce.
2. No sequence of model outputs reaches `completed` without the ledger showing the whole cycle
   (Phase 12): RED's tests written and run, GREEN passing, and the mandatory REFACTOR passing
   with enough line + branch coverage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.query.config import build_run_config
from app.query.deps import LoopDeps
from app.query.query import run_loop
from app.query.state import initial_loop_state
from app.query.transitions import Terminal, Terminated
from app.services.tools.tool_orchestration import run_tools
from app.state.app_state_store import AppState, AppStateStore
from app.tdd.coverage_gate import CoverageReading, CoverageStatus
from app.tdd.ledger import PhaseLedger, TddPhase
from app.tdd.phase_permissions import (
    IMPLEMENTATION_WRITER_TOOL_NAMES,
    is_implementation_writing_tool,
)
from app.tdd.run_tests_tool.run_tests_tool import (
    SuiteExecutionResult,
    build_run_tests_tool,
)
from app.tdd.stop_hook import tdd_phase_incomplete_hook
from app.tool import BuiltTool, Tool, ToolResult, build_tool, tool_context_for
from app.tools.tools import assemble_tool_pool
from app.types.permissions import (
    PermissionBehavior,
    PermissionMode,
    ToolPermissionContext,
)
from app.utils.permissions.permissions import has_permissions_to_use_tool
from tests.conftest import FakeWorkspace


def make_test_tool(name: str, *, is_impl: bool = False, is_test: bool = False) -> BuiltTool:
    def call(args: dict, ctx: Any) -> ToolResult:
        if is_test and ctx.workspace is not None:  # a test writer really writes a test file
            ctx.workspace.files[f"tests/test_{len(ctx.workspace.files)}.py"] = "def test_x():\n    pass\n"
        return ToolResult(content=f"{name} executed")

    return build_tool(
        name=name,
        prompt=f"Tool {name}",
        call=call,
        is_implementation_writer=is_impl,
        is_test_writer=is_test,
    )


class ScriptedModel:
    """Fake model yielding a sequence of scripted turns."""

    def __init__(self, turns: list[list[AIMessage]]) -> None:
        self.turns = list(turns)

    async def __call__(self, state, config) -> AsyncIterator[AIMessage]:
        if self.turns:
            messages = self.turns.pop(0)
            for m in messages:
                yield m
        else:
            yield AIMessage(content="default stop")


# ==============================================================================
# Invariant 1:
# "No reachable tool pool in RED contains an implementation-writing tool,
# for any ledger state the loop can produce."
# ==============================================================================


class TestInvariant1NoImplementationWriterInRedPool:
    """
    Property 1: For all reachable ledger states where phase == RED,
    no implementation-writing tool exists in assemble_tool_pool().
    """

    @pytest.fixture
    def candidate_tool_roster(self) -> list[Tool]:
        return [
            # Dedicated implementation writers
            make_test_tool("WriteImplementation", is_impl=True),
            make_test_tool("WriteCode", is_impl=True),
            make_test_tool("EditImplementation", is_impl=True),
            make_test_tool("EditCode", is_impl=True),
            make_test_tool("CustomCodeGenerator", is_impl=True),
            # Test writers
            make_test_tool("WriteTest", is_test=True),
            make_test_tool("EditTest", is_test=True),
            make_test_tool("CustomTestGenerator", is_test=True),
            # General tools
            make_test_tool("WriteFile"),
            make_test_tool("Edit"),
            make_test_tool("ReadFile"),
            make_test_tool("ListDir"),
            make_test_tool("Grep"),
            # Test runner
            build_run_tests_tool(),
        ]

    def reachable_red_ledger_states(self) -> list[PhaseLedger]:
        """All reachable ledger states in RED produced by loop operations."""
        return [
            # Initial state
            PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=False),
            # F2 green in red: test passed on first run without prior failure
            PhaseLedger(phase=TddPhase.RED, tests_written=False, green_passed=True),
            # Red state with string phase
            PhaseLedger(phase="RED", tests_written=False, green_passed=False),
        ]

    def test_property_1_pool_assembly_contains_no_implementation_writer(
        self, candidate_tool_roster: list[Tool]
    ):
        for ledger in self.reachable_red_ledger_states():
            pool = assemble_tool_pool(candidate_tool_roster, phase_ledger=ledger)
            pool_tool_names = {tool.name for tool in pool}

            # Check known implementation writers
            for impl_name in IMPLEMENTATION_WRITER_TOOL_NAMES:
                assert impl_name not in pool_tool_names, (
                    f"Implementation writer '{impl_name}' found in tool pool for RED state: {ledger}"
                )

            # Check every tool in pool against predicate
            for tool in pool:
                assert not is_implementation_writing_tool(tool), (
                    f"Tool '{tool.name}' in pool satisfies is_implementation_writing_tool for RED state: {ledger}"
                )
                assert not tool.is_implementation_writer(), (
                    f"Tool '{tool.name}' in pool has is_implementation_writer() == True in RED state: {ledger}"
                )

            # RunTests must remain present
            assert "RunTests" in pool_tool_names, f"RunTests was stripped from RED pool: {ledger}"

    def test_property_1_runtime_gate_denies_implementation_writers_in_red(self):
        async def go():
            for ledger in self.reachable_red_ledger_states():
                store = AppStateStore(AppState(phase_ledger=ledger))
                perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
                ctx = tool_context_for(store, permission_context=perm_ctx)

                # Direct call to implementation writer
                impl_tool = make_test_tool("WriteImplementation", is_impl=True)
                gate_res = await has_permissions_to_use_tool(impl_tool, {}, ctx)
                assert gate_res.behavior == PermissionBehavior.DENY

                # Generic writer targeting production file
                write_file = make_test_tool("WriteFile")
                gate_res2 = await has_permissions_to_use_tool(write_file, {"path": "src/app.py"}, ctx)
                assert gate_res2.behavior == PermissionBehavior.DENY

                # Generic writer targeting test file is allowed
                gate_res3 = await has_permissions_to_use_tool(write_file, {"path": "tests/test_app.py"}, ctx)
                assert gate_res3.behavior == PermissionBehavior.ALLOW

        asyncio.run(go())


# ==============================================================================
# Invariant 2:
# "No sequence of model outputs reaches `completed` without the ledger showing Red-then-Green."
# ==============================================================================


MET = CoverageReading(CoverageStatus.MEASURED, 95.0)
UNDER = CoverageReading(CoverageStatus.MEASURED, 60.0)


def call(name: str, cid: str) -> dict:
    return {"id": cid, "type": "tool_call", "name": name, "args": {"test_path": "tests"} if name == "RunTests" else {}}


class TestInvariant2NoCompletionWithoutTheWholeCycle:
    """
    Property 2: No sequence of model outputs reaches `completed` without the ledger showing
    the whole cycle (tests_written, green_passed and refactor_passed).
    """

    def _build_test_deps(self, model: ScriptedModel, results: list[SuiteExecutionResult], events: list) -> LoopDeps:
        pending = list(results)

        def runner(test_path: str):
            return pending.pop(0) if pending else SuiteExecutionResult(exit_code=0)

        run_tests_tool = build_run_tests_tool(runner)
        tools = [
            make_test_tool("WriteTest", is_test=True),
            make_test_tool("WriteImplementation", is_impl=True),
            run_tests_tool,
        ]

        async def run_tools_impl(calls, assistant_msgs, state, config):
            async for r in run_tools(calls, assistant_msgs, state, config, tools=tools):
                yield r

        async def fake_compact(s, c):
            from app.query.state import CompactionResult
            return CompactionResult(compacted=False, messages=s.messages)

        return LoopDeps(
            call_model=model,
            compact=fake_compact,
            uuid=lambda: "u1",
            now=lambda: 1000.0,
            run_tools=run_tools_impl,
            stop_hooks=tdd_phase_incomplete_hook,
            emit_event=events.append,
        )

    async def _run(self, model: ScriptedModel, results: list[SuiteExecutionResult]) -> tuple[Terminated, AppStateStore]:
        deps = self._build_test_deps(model, results, [])
        store = AppStateStore()
        ctx = tool_context_for(store, workspace=FakeWorkspace())
        state = initial_loop_state((HumanMessage(content="task"),), ctx)
        terminated: Terminated | None = None
        async for ev in run_loop(state, build_run_config("test-p2", postgres_checkpointing=False), deps):
            if isinstance(ev, Terminated):
                terminated = ev
        assert terminated is not None
        return terminated, store

    def test_property_2_pure_text_stops_without_tools_cannot_reach_completed(self):
        """Model immediately outputs text claiming done: blocks and aborts via stop hook."""
        async def go():
            model = ScriptedModel([
                [AIMessage(content="I claim completion immediately without tests.")],
                [AIMessage(content="I still claim done without writing tests.")],
            ])
            events: list = []
            deps = self._build_test_deps(model, [], events)
            store = AppStateStore()
            ctx = tool_context_for(store)
            state = initial_loop_state((HumanMessage(content="task"),), ctx)
            config = build_run_config("test-p2", postgres_checkpointing=False)

            terminated: Terminated | None = None
            async for ev in run_loop(state, config, deps):
                if isinstance(ev, Terminated):
                    terminated = ev

            assert terminated is not None
            # Must NOT reach COMPLETED
            assert terminated.reason != Terminal.COMPLETED
            assert terminated.reason == Terminal.STOP_HOOK_PREVENTED
            assert not store.get().phase_ledger.is_cycle_complete

        asyncio.run(go())

    def test_property_2_running_tests_without_writing_any_cannot_reach_completed(self):
        """Passing tests the item did not write are not RED's evidence."""
        model = ScriptedModel([
            [AIMessage(content="", tool_calls=[call("RunTests", "c1")])],
            [AIMessage(content="Tests passed on first run! I am done.")],
            [AIMessage(content="I insist I am done.")],
        ])
        terminated, store = asyncio.run(self._run(model, [SuiteExecutionResult(exit_code=0, coverage=MET)]))
        assert terminated.reason == Terminal.STOP_HOOK_PREVENTED
        assert store.get().phase_ledger == PhaseLedger()

    @pytest.mark.parametrize("results, ledger_phase", [
        # GREEN never passes
        ([SuiteExecutionResult(exit_code=1), SuiteExecutionResult(exit_code=1)], TddPhase.GREEN),
        # REFACTOR never run
        ([SuiteExecutionResult(exit_code=1), SuiteExecutionResult(exit_code=0, coverage=MET)], TddPhase.REFACTOR),
        # REFACTOR under the coverage threshold
        ([SuiteExecutionResult(exit_code=1), SuiteExecutionResult(exit_code=0, coverage=UNDER),
          SuiteExecutionResult(exit_code=0, coverage=UNDER)], TddPhase.REFACTOR),
        # REFACTOR breaks a test
        ([SuiteExecutionResult(exit_code=1), SuiteExecutionResult(exit_code=0, coverage=MET),
          SuiteExecutionResult(exit_code=1, coverage=MET)], TddPhase.REFACTOR),
    ])
    def test_property_2_an_unfinished_cycle_cannot_reach_completed(self, results, ledger_phase):
        turns = [[AIMessage(content="", tool_calls=[call("WriteTest", "w")])]]
        turns += [[AIMessage(content="", tool_calls=[call("RunTests", f"r{n}")])] for n in range(len(results))]
        turns += [[AIMessage(content="done")], [AIMessage(content="really done")]]
        terminated, store = asyncio.run(self._run(ScriptedModel(turns), results))
        assert terminated.reason == Terminal.STOP_HOOK_PREVENTED
        assert store.get().phase_ledger.phase == ledger_phase
        assert not store.get().phase_ledger.is_cycle_complete

    def test_property_2_the_whole_cycle_reaches_completed(self):
        """Tests written and run, implementation passing, REFACTOR passing with enough coverage."""
        model = ScriptedModel([
            [AIMessage(content="Writing robust tests.", tool_calls=[call("WriteTest", "c0")])],
            [AIMessage(content="Running them.", tool_calls=[call("RunTests", "c1")])],
            [AIMessage(content="Now implementing.", tool_calls=[call("WriteImplementation", "c2")])],
            [AIMessage(content="Verifying GREEN.", tool_calls=[call("RunTests", "c3")])],
            [AIMessage(content="Refactoring, then measuring.", tool_calls=[call("RunTests", "c4")])],
            [AIMessage(content="Cycle complete! All tests pass.")],
        ])
        terminated, store = asyncio.run(self._run(model, [
            SuiteExecutionResult(exit_code=0), SuiteExecutionResult(exit_code=0, coverage=MET),
            SuiteExecutionResult(exit_code=0, coverage=MET)]))
        assert terminated.reason == Terminal.COMPLETED
        assert store.get().phase_ledger == PhaseLedger(phase=TddPhase.REFACTOR, tests_written=True, green_passed=True,
                                                       refactor_passed=True, coverage=95.0)
