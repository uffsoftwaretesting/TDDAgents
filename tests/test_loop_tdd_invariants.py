"""
Structural Invariant Property Tests for Part D (D6).

These two tests represent the thesis's structural claim in executable form
(§3.3 and §6.2 of `docs/transition_elaboration_plan.md`):

1. No reachable tool pool in RED contains an implementation-writing tool,
   for any ledger state the loop can produce.
2. No sequence of model outputs reaches `completed` without the ledger showing Red-then-Green.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import LoopDeps
from app.loop.engine import run_loop
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.gate import has_permissions_to_use_tool
from app.loop.permissions.tdd import (
    IMPLEMENTATION_WRITER_TOOL_NAMES,
    is_implementation_writing_tool,
)
from app.loop.permissions.types import PermissionBehavior, PermissionMode, ToolPermissionContext
from app.loop.state import initial_loop_state
from app.loop.tdd.hooks import tdd_phase_incomplete_hook
from app.loop.tools.base import BuiltTool, Tool, build_tool
from app.loop.tools.orchestration import run_tools
from app.loop.tools.pool import assemble_tool_pool
from app.loop.tools.run_tests import SuiteExecutionResult, build_run_tests_tool
from app.loop.tools.types import ToolResult
from app.loop.transitions import Terminal, Terminated


def make_test_tool(name: str, *, is_impl: bool = False, is_test: bool = False) -> BuiltTool:
    return build_tool(
        name=name,
        prompt=f"Tool {name}",
        call=lambda args, ctx: ToolResult(content=f"{name} executed"),
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
            PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=False),
            # F2 green in red: test passed on first run without prior failure
            PhaseLedger(phase=TddPhase.RED, red_confirmed=False, green_passed=True),
            # Red state with string phase
            PhaseLedger(phase="RED", red_confirmed=False, green_passed=False),
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


class TestInvariant2NoCompletionWithoutRedThenGreen:
    """
    Property 2: No sequence of model outputs reaches `completed` without the ledger
    showing Red-then-Green (red_confirmed=True and green_passed=True).
    """

    def _build_test_deps(self, model: ScriptedModel, test_exit_codes: list[int], events: list) -> LoopDeps:
        exit_codes = list(test_exit_codes)

        def runner(test_path: str):
            code = exit_codes.pop(0) if exit_codes else 0
            return SuiteExecutionResult(exit_code=code, stdout=f"exit {code}")

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
            from app.loop.deps import CompactionResult
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

    def test_property_2_f2_green_in_red_cannot_reach_completed(self):
        """Model runs tests which pass on first run (F2): red_confirmed is False, cannot complete."""
        async def go():
            tool_call_run_tests = {
                "id": "c1",
                "type": "tool_call",
                "name": "RunTests",
                "args": {"test_path": "tests/test_x.py"},
            }
            model = ScriptedModel([
                # Turn 1: model runs tests immediately
                [AIMessage(content="", tool_calls=[tool_call_run_tests])],
                # Turn 2: tests passed (exit 0) without red confirmed. Model claims done.
                [AIMessage(content="Tests passed on first run! I am done.")],
                # Turn 3: hook blocked, model claims done again.
                [AIMessage(content="I insist I am done.")],
            ])
            events: list = []
            # Test passes on first run: exit 0
            deps = self._build_test_deps(model, [0], events)
            store = AppStateStore()
            ctx = tool_context_for(store)
            state = initial_loop_state((HumanMessage(content="task"),), ctx)
            config = build_run_config("test-p2-f2", postgres_checkpointing=False)

            terminated: Terminated | None = None
            async for ev in run_loop(state, config, deps):
                if isinstance(ev, Terminated):
                    terminated = ev

            assert terminated is not None
            # Must NOT reach COMPLETED
            assert terminated.reason != Terminal.COMPLETED
            assert terminated.reason == Terminal.STOP_HOOK_PREVENTED
            # Ledger shows green passed but red never confirmed
            ledger = store.get().phase_ledger
            assert ledger.red_confirmed is False
            assert ledger.green_passed is True
            assert not ledger.is_cycle_complete

        asyncio.run(go())

    def test_property_2_red_then_green_successfully_reaches_completed(self):
        """Model executes full Red-then-Green cycle: reaches COMPLETED."""
        async def go():
            call_run_tests_red = {
                "id": "c1",
                "type": "tool_call",
                "name": "RunTests",
                "args": {"test_path": "tests/test_x.py"},
            }
            call_impl = {
                "id": "c2",
                "type": "tool_call",
                "name": "WriteImplementation",
                "args": {},
            }
            call_run_tests_green = {
                "id": "c3",
                "type": "tool_call",
                "name": "RunTests",
                "args": {"test_path": "tests/test_x.py"},
            }
            model = ScriptedModel([
                # Turn 1: run tests, fails (RED confirmed)
                [AIMessage(content="Running tests to confirm RED.", tool_calls=[call_run_tests_red])],
                # Turn 2: in GREEN, writes implementation
                [AIMessage(content="Now implementing.", tool_calls=[call_impl])],
                # Turn 3: runs tests again, passes (GREEN passed)
                [AIMessage(content="Verifying GREEN.", tool_calls=[call_run_tests_green])],
                # Turn 4: claims done now that cycle is complete!
                [AIMessage(content="Cycle complete! All tests pass.")],
            ])
            events: list = []
            # First test run exit 1 (fail), second test run exit 0 (pass)
            deps = self._build_test_deps(model, [1, 0], events)
            store = AppStateStore()
            ctx = tool_context_for(store)
            state = initial_loop_state((HumanMessage(content="task"),), ctx)
            config = build_run_config("test-p2-success", postgres_checkpointing=False)

            terminated: Terminated | None = None
            async for ev in run_loop(state, config, deps):
                if isinstance(ev, Terminated):
                    terminated = ev

            assert terminated is not None
            # Must reach COMPLETED
            assert terminated.reason == Terminal.COMPLETED
            # Ledger is Red-then-Green
            ledger = store.get().phase_ledger
            assert ledger.red_confirmed is True
            assert ledger.green_passed is True
            assert ledger.is_cycle_complete is True

        asyncio.run(go())
