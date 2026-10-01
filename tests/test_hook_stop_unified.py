"""
Tests for Unified Stop Hooks, TDD Lifecycle Enforcement, and Tool Middleware Integration (Part H5).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from app.hooks.config import HookMatcher, HookSettings
from app.hooks.dispatcher import HookDispatcher, HookOutcome
from app.hooks.schemas import CommandHook
from app.hooks.stop_hooks import build_stop_hooks_runner
from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.deps import StopHookResult
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.permissions.types import PermissionMode, ToolPermissionContext
from app.loop.state import LoopState, initial_loop_state
from app.loop.tdd.hooks import tdd_phase_incomplete_hook
from app.loop.tools.base import Tool, ToolResult, build_tool
from app.loop.tools.execution import run_tool_use


def make_loop_state(
    *,
    phase: TddPhase = TddPhase.RED,
    red_confirmed: bool = False,
    green_passed: bool = False,
    stop_hook_active: bool = False,
    hook_dispatcher: HookDispatcher | None = None,
) -> LoopState:
    import dataclasses

    ledger = PhaseLedger(phase=phase, red_confirmed=red_confirmed, green_passed=green_passed)
    store = AppStateStore(state=AppState(phase_ledger=ledger))
    tool_ctx = tool_context_for(store, hook_dispatcher=hook_dispatcher)
    state = initial_loop_state((HumanMessage(content="task"),), tool_ctx)
    if stop_hook_active:
        state = dataclasses.replace(state, stop_hook_active=True)
    return state


class TestTddPhaseIncompleteHook:
    @pytest.mark.anyio
    async def test_cycle_complete_allows_turn_exit(self):
        state = make_loop_state(phase=TddPhase.GREEN, red_confirmed=True, green_passed=True)
        config = build_run_config("test-run", postgres_checkpointing=False)
        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert result.blocking_errors == ()

    @pytest.mark.anyio
    async def test_red_phase_unconfirmed_blocks(self):
        state = make_loop_state(phase=TddPhase.RED, red_confirmed=False, green_passed=False)
        config = build_run_config("test-run", postgres_checkpointing=False)
        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert "no failing test observed yet in RED phase" in result.blocking_errors[0].content

    @pytest.mark.anyio
    async def test_f2_green_in_red_blocks_with_specific_explanation(self):
        state = make_loop_state(phase=TddPhase.RED, red_confirmed=False, green_passed=True)
        config = build_run_config("test-run", postgres_checkpointing=False)
        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert "tests passed on first run without prior failure" in result.blocking_errors[0].content

    @pytest.mark.anyio
    async def test_green_phase_unpassed_blocks(self):
        state = make_loop_state(phase=TddPhase.GREEN, red_confirmed=True, green_passed=False)
        config = build_run_config("test-run", postgres_checkpointing=False)
        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is False
        assert len(result.blocking_errors) == 1
        assert "tests are not passing yet in GREEN phase" in result.blocking_errors[0].content

    @pytest.mark.anyio
    async def test_stop_hook_active_latch_prevents_infinite_loop(self):
        """
        D5 / H5 Invariant:
        When stop_hook_active is True on an incomplete cycle, the hook terminates
        the turn with prevent_continuation=True to prevent an infinite recovery loop.
        """
        state = make_loop_state(
            phase=TddPhase.RED,
            red_confirmed=False,
            green_passed=False,
            stop_hook_active=True,
        )
        config = build_run_config("test-run", postgres_checkpointing=False)
        result = await tdd_phase_incomplete_hook(state, config)
        assert result.prevent_continuation is True
        assert result.blocking_errors == ()


class TestPrecedenceRule:
    def test_prevent_continuation_blanks_blocking_errors(self):
        result = StopHookResult(
            prevent_continuation=True,
            blocking_errors=(HumanMessage(content="will be blanked"),),
        )
        assert result.prevent_continuation is True
        assert result.blocking_errors == ()


class TestBuildStopHooksRunner:
    @pytest.mark.anyio
    async def test_complete_cycle_runs_configured_command_stop_hook(self, tmp_path):
        # Complete TDD cycle so TDD hook allows exit
        # Configured stop hook blocks on Stop event
        settings = HookSettings(
            events={
                "Stop": (
                    HookMatcher(
                        matcher=None,
                        hooks=(CommandHook(command="echo 'final check failed' >&2; exit 2"),),
                    ),
                )
            }
        )
        dispatcher = HookDispatcher(settings, project_root=tmp_path)
        stop_hooks_fn = build_stop_hooks_runner(dispatcher=dispatcher)

        state = make_loop_state(
            phase=TddPhase.GREEN,
            red_confirmed=True,
            green_passed=True,
        )
        config = build_run_config("test-run", postgres_checkpointing=False)
        res = await stop_hooks_fn(state, config)
        assert res.prevent_continuation is False
        assert len(res.blocking_errors) == 1
        assert "final check failed" in res.blocking_errors[0].content

    @pytest.mark.anyio
    async def test_configured_hook_can_prevent_continuation(self, tmp_path):
        json_doc = json.dumps({"preventContinuation": True})
        settings = HookSettings(
            events={
                "Stop": (
                    HookMatcher(
                        matcher=None,
                        hooks=(CommandHook(command=f"echo '{json_doc}'"),),
                    ),
                )
            }
        )
        dispatcher = HookDispatcher(settings, project_root=tmp_path)
        stop_hooks_fn = build_stop_hooks_runner(dispatcher=dispatcher)

        state = make_loop_state(
            phase=TddPhase.GREEN,
            red_confirmed=True,
            green_passed=True,
        )
        config = build_run_config("test-run", postgres_checkpointing=False)
        res = await stop_hooks_fn(state, config)
        assert res.prevent_continuation is True
        assert res.blocking_errors == ()


class TestToolHookMiddlewareIntegration:
    @pytest.mark.anyio
    async def test_pre_tool_use_denial_blocks_tool_call(self):
        # Dispatcher that denies PreToolUse
        dispatcher = MagicMock(spec=HookDispatcher)
        dispatcher.run.return_value = HookOutcome(denied=True, reason="Security veto")

        store = AppStateStore()
        ctx = tool_context_for(store, hook_dispatcher=dispatcher)

        dummy_tool = MagicMock(spec=Tool)
        dummy_tool.name = "Bash"
        dummy_tool.validate_input.return_value = MagicMock(valid=True)

        outcome = await run_tool_use(
            {"id": "call_1", "name": "Bash", "args": {"command": "rm -rf /"}},
            HumanMessage(content="run this"),
            ctx,
            [dummy_tool],
        )

        assert outcome.message.status == "error"
        assert "Security veto" in outcome.message.content
        dummy_tool.call.assert_not_called()

    @pytest.mark.anyio
    async def test_pre_tool_use_rewrites_input(self):
        dispatcher = MagicMock(spec=HookDispatcher)
        dispatcher.run.side_effect = [
            # PreToolUse returns updatedInput
            HookOutcome(denied=False, updated_input={"command": "echo safe"}),
            # PostToolUse proceeds
            HookOutcome(denied=False),
        ]

        store = AppStateStore(state=AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
        perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
        ctx = tool_context_for(store, permission_context=perm_ctx, hook_dispatcher=dispatcher)

        called_args = []

        async def _call(args, context):
            called_args.append(args)
            return ToolResult(content=f"ran {args.get('command')}")

        tool = build_tool(name="Bash", prompt="Run bash commands", call=_call)

        outcome = await run_tool_use(
            {"id": "call_2", "name": "Bash", "args": {"command": "echo dangerous"}},
            HumanMessage(content="run this"),
            ctx,
            [tool],
        )

        assert len(called_args) == 1
        assert called_args[0] == {"command": "echo safe"}
        assert "ran echo safe" in outcome.message.content

    @pytest.mark.anyio
    async def test_post_tool_use_attaches_feedback(self):
        dispatcher = MagicMock(spec=HookDispatcher)
        dispatcher.run.side_effect = [
            # PreToolUse proceeds
            HookOutcome(denied=False),
            # PostToolUse returns additionalContext
            HookOutcome(denied=False, additional_context="Code style note: 2 spaces"),
        ]

        store = AppStateStore(state=AppState(phase_ledger=PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)))
        perm_ctx = ToolPermissionContext(mode=PermissionMode.BYPASS_PERMISSIONS)
        ctx = tool_context_for(store, permission_context=perm_ctx, hook_dispatcher=dispatcher)

        async def _call(args, context):
            return ToolResult(content="File saved")

        tool = build_tool(name="Write", prompt="Write file contents", call=_call)

        outcome = await run_tool_use(
            {"id": "call_3", "name": "Write", "args": {"file_path": "a.py"}},
            HumanMessage(content="write this"),
            ctx,
            [tool],
        )

        assert "Code style note: 2 spaces" in outcome.message.content
