"""
Hooks reaching tools through `execute_tool`.

The dispatcher and the tool pipeline each have their own suite; this covers the seam
between them, which is where a contract mismatch would otherwise hide — a hook that vetoes
correctly in isolation but whose veto never stops the call.
"""

from __future__ import annotations

import json

from app.hooks.config import HookCommand, HookMatcher, HookSettings
from app.hooks.dispatcher import HookDispatcher
from app.tools.base import Capability, ToolResult, execute_tool
from tests.conftest import CommandArgs, make_read_tool, make_tool


def script_dispatcher(tmp_path, body: str, event: str = "PreToolUse", **kwargs) -> HookDispatcher:
    """A hook whose command text shares nothing with its output. See test_hook_dispatcher."""
    path = tmp_path / f"hook-{event}.sh"
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return dispatcher_for(str(path), event, **kwargs)


def dispatcher_for(command: str, event: str = "PreToolUse", **kwargs) -> HookDispatcher:
    settings = HookSettings(
        events={event: (HookMatcher(matcher=None, hooks=(HookCommand(command=command, **kwargs),)),)}
    )
    return HookDispatcher(settings)


def bash_tool():
    """A Bash-shaped tool: carries `command`, so hook `if:` conditions can match on it."""
    return make_tool(
        "Bash",
        args_schema=CommandArgs,
        call=lambda args, ctx: ToolResult(tool_name="Bash", content=f"ran: {args.command}"),
        required_capability=lambda args: Capability.EXECUTE,
    )


class TestPreToolUseThroughExecuteTool:
    def test_veto_stops_the_call(self, make_tool_ctx, tmp_path):
        marker = tmp_path / "ran"
        tool = make_tool(
            "Bash",
            args_schema=CommandArgs,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content=marker.write_text("x") or ""),
            required_capability=lambda args: Capability.EXECUTE,
        )
        ctx = make_tool_ctx(
            hook_dispatcher=script_dispatcher(tmp_path, "echo 'policy forbids this' >&2; exit 2")
        )

        result = execute_tool(tool, {"command": "pip install x"}, ctx)

        assert result.is_error is True
        assert "policy forbids this" in result.content
        assert not marker.exists(), "the tool body must never run after a veto"

    def test_allowed_call_proceeds(self, make_tool_ctx):
        ctx = make_tool_ctx(hook_dispatcher=dispatcher_for("exit 0"))
        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert result.is_error is False
        assert result.content == "ran: ls"

    def test_updated_input_rewrites_the_call(self, make_tool_ctx):
        document = {"updatedInput": {"command": "pip install --dry-run x"}}
        ctx = make_tool_ctx(hook_dispatcher=dispatcher_for(f"echo '{json.dumps(document)}'"))

        result = execute_tool(bash_tool(), {"command": "pip install x"}, ctx)

        assert result.content == "ran: pip install --dry-run x"

    def test_invalid_updated_input_is_rejected_rather_than_crashing(self, make_tool_ctx):
        document = {"updatedInput": {"wrong_field": 1}}
        ctx = make_tool_ctx(hook_dispatcher=dispatcher_for(f"echo '{json.dumps(document)}'"))

        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)

        assert result.is_error is True
        assert "invalid updatedInput" in result.content

    def test_if_condition_sees_the_command(self, make_tool_ctx):
        ctx = make_tool_ctx(
            hook_dispatcher=dispatcher_for("exit 2", if_condition="Bash(pip *)")
        )
        assert execute_tool(bash_tool(), {"command": "pip install x"}, ctx).is_error is True
        assert execute_tool(bash_tool(), {"command": "ls -la"}, ctx).is_error is False

    def test_veto_happens_before_the_capability_gate(self, make_tool_ctx, tmp_path):
        """Both reject; the hook's reason is the more useful one to surface."""
        ctx = make_tool_ctx(
            permission_mode="read_only",
            hook_dispatcher=script_dispatcher(tmp_path, "echo 'hook first' >&2; exit 2"),
        )
        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert "hook first" in result.content

    def test_hook_does_not_run_for_invalid_arguments(self, make_tool_ctx, tmp_path):
        """Schema validation is cheaper than a process spawn, so it comes first."""
        log = tmp_path / "log"
        ctx = make_tool_ctx(hook_dispatcher=dispatcher_for(f"echo ran > {log}"))
        execute_tool(bash_tool(), {}, ctx)
        assert not log.exists()


class TestPostToolUseThroughExecuteTool:
    def test_result_survives_a_flag_and_carries_the_feedback(self, make_tool_ctx, tmp_path):
        ctx = make_tool_ctx(
            hook_dispatcher=script_dispatcher(
                tmp_path, "echo 'coverage dropped' >&2; exit 2", event="PostToolUse"
            )
        )
        result = execute_tool(bash_tool(), {"command": "pytest"}, ctx)

        assert result.is_error is False
        assert result.content == "ran: pytest"
        assert "coverage dropped" in result.additional_context
        assert result.hook_stopped_continuation is True
        assert "coverage dropped" in result.rendered()

    def test_continue_false_marks_the_step(self, make_tool_ctx):
        ctx = make_tool_ctx(
            hook_dispatcher=dispatcher_for('echo \'{"continue": false}\'', event="PostToolUse")
        )
        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert result.hook_stopped_continuation is True
        assert result.is_error is False

    def test_additional_context_without_a_stop(self, make_tool_ctx):
        document = {"additionalContext": "prefer pathlib here"}
        ctx = make_tool_ctx(
            hook_dispatcher=dispatcher_for(f"echo '{json.dumps(document)}'", event="PostToolUse")
        )
        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert result.additional_context == "prefer pathlib here"
        assert result.hook_stopped_continuation is False

    def test_post_hook_sees_the_tool_response(self, make_tool_ctx, tmp_path):
        capture = tmp_path / "payload.json"
        ctx = make_tool_ctx(
            hook_dispatcher=dispatcher_for(f"cat > {capture}", event="PostToolUse")
        )
        execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert json.loads(capture.read_text())["tool_response"] == "ran: ls"

    def test_post_hook_sees_the_governed_result_not_the_raw_one(self, make_tool_ctx, tmp_path):
        """Governance runs first, so a hook is never handed a 500 KB payload on stdin."""
        capture = tmp_path / "payload.json"
        big = make_read_tool(
            "Bash",
            max_result_chars=1_000,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content="y" * 50_000),
        )
        ctx = make_tool_ctx(
            hook_dispatcher=dispatcher_for(f"cat > {capture}", event="PostToolUse")
        )
        execute_tool(big, {}, ctx)

        assert len(json.loads(capture.read_text())["tool_response"]) < 5_000


class TestBothEvents:
    def test_contexts_from_both_events_accumulate(self, make_tool_ctx):
        pre = HookCommand(command='echo \'{"additionalContext": "before"}\'')
        post = HookCommand(command='echo \'{"additionalContext": "after"}\'')
        settings = HookSettings(
            events={
                "PreToolUse": (HookMatcher(matcher=None, hooks=(pre,)),),
                "PostToolUse": (HookMatcher(matcher=None, hooks=(post,)),),
            }
        )
        ctx = make_tool_ctx(hook_dispatcher=HookDispatcher(settings))

        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)

        assert "before" in result.additional_context
        assert "after" in result.additional_context

    def test_no_dispatcher_is_the_normal_case(self, make_tool_ctx):
        ctx = make_tool_ctx(hook_dispatcher=None)
        result = execute_tool(bash_tool(), {"command": "ls"}, ctx)
        assert result.is_error is False
        assert result.additional_context == ""
        assert result.hook_stopped_continuation is False

    def test_a_broken_hook_does_not_break_the_tool(self, make_tool_ctx):
        ctx = make_tool_ctx(hook_dispatcher=dispatcher_for("nonexistent-command-abc"))
        assert execute_tool(bash_tool(), {"command": "ls"}, ctx).is_error is False
