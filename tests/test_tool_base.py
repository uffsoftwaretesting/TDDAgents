"""
The Tool protocol, its fail-closed factory, and the `execute_tool` pipeline.

The pipeline is the thing worth testing hardest: it is the only place validation, both
hook events, both enforcement gates, result governance and the sync checkpoint happen, so
a gap here is a gap for all 18 tools at once.
"""

from __future__ import annotations

import pytest

from app.config.config import Config
from app.tools.base import (
    BASH_MAX_RESULT_CHARS,
    CAPABILITIES_BY_MODE,
    DEFAULT_MAX_RESULT_CHARS,
    GREP_MAX_RESULT_CHARS,
    TOOL_DEFAULTS,
    PREVIEW_HEAD_CHARS,
    PREVIEW_TAIL_CHARS,
    UNBOUNDED_RESULT_CHARS,
    CancelToken,
    Capability,
    PermissionResult,
    ToolResult,
    ValidationResult,
    execute_tool,
)
from app.hooks.dispatcher import HookEvent
from app.workspace.base import WorkspaceError
from tests.conftest import CommandArgs, EchoArgs, make_read_tool, make_tool


class TestBuildToolDefaults:
    """An omitted method must always be the safe answer."""

    def test_fail_closed_predicates(self):
        tool = make_tool()
        args = EchoArgs()
        assert tool.is_read_only(args) is False
        assert tool.is_concurrency_safe(args) is False
        assert tool.is_destructive(args) is False

    def test_defaults_to_write_capability(self):
        """Not `read`: a tool that forgot to declare itself must not slip past the gate."""
        assert make_tool().required_capability(EchoArgs()) is Capability.WRITE

    def test_enabled_and_unpinned_by_default(self):
        tool = make_tool()
        assert tool.is_enabled() is True
        assert tool.required_workspace(EchoArgs()) is None

    def test_permissive_validation_and_permissions_by_default(self):
        tool = make_tool()
        ctx = None  # unused by the defaults
        assert tool.validate_input(EchoArgs(), ctx).ok is True
        assert tool.check_permissions(EchoArgs(), ctx).behavior == "allow"

    def test_default_result_limit(self):
        assert make_tool().max_result_chars == DEFAULT_MAX_RESULT_CHARS

    def test_overrides_win_over_defaults(self):
        tool = make_tool(is_read_only=lambda args: True, max_result_chars=5)
        assert tool.is_read_only(EchoArgs()) is True
        assert tool.max_result_chars == 5

    def test_user_facing_name_defaults_to_name(self):
        assert make_tool("Grep").user_facing_name == "Grep"

    def test_unknown_override_is_rejected(self):
        """A typo'd override must fail loudly at definition time, not silently at runtime."""
        with pytest.raises(TypeError, match="unexpected argument"):
            make_tool(is_readonly=lambda args: True)

    def test_every_default_key_is_a_protocol_member(self):
        tool = make_tool()
        for key in TOOL_DEFAULTS:
            assert hasattr(tool, key)

    def test_prompt_is_returned_by_method(self):
        assert make_tool("Grep").prompt() == "The Grep tool."


class TestResultLimits:
    def test_roster_limits_are_distinct(self):
        """Per-tool, not one global number — the whole point of the field."""
        assert GREP_MAX_RESULT_CHARS == 20_000
        assert BASH_MAX_RESULT_CHARS == 30_000
        assert DEFAULT_MAX_RESULT_CHARS == 100_000
        assert UNBOUNDED_RESULT_CHARS > DEFAULT_MAX_RESULT_CHARS


class TestCapabilityLadder:
    def test_modes_are_strictly_nested(self):
        read_only = CAPABILITIES_BY_MODE["read_only"]
        write = CAPABILITIES_BY_MODE["workspace_write"]
        full = CAPABILITIES_BY_MODE["full"]
        assert read_only < write < full

    def test_read_only_admits_only_read(self):
        assert CAPABILITIES_BY_MODE["read_only"] == frozenset({Capability.READ})

    def test_workspace_write_excludes_execute(self):
        assert Capability.EXECUTE not in CAPABILITIES_BY_MODE["workspace_write"]

    def test_full_admits_everything(self):
        assert CAPABILITIES_BY_MODE["full"] == frozenset(Capability)


class TestExecuteToolHappyPath:
    def test_returns_the_tools_result(self, tool_ctx):
        result = execute_tool(make_read_tool(), {"text": "hi"}, tool_ctx)
        assert result.content == "hi"
        assert result.is_error is False

    def test_stamps_call_index_and_id(self, tool_ctx):
        result = execute_tool(make_read_tool(), {}, tool_ctx, call_index=3, call_id="abc")
        assert result.call_index == 3
        assert result.call_id == "abc"

    def test_defaults_stamp_to_zero(self, tool_ctx):
        result = execute_tool(make_read_tool(), {}, tool_ctx)
        assert result.call_index == 0
        assert result.call_id == ""


class TestExecuteToolValidation:
    def test_schema_violation_is_an_error_result_not_an_exception(self, tool_ctx):
        tool = make_tool(args_schema=CommandArgs)
        result = execute_tool(tool, {}, tool_ctx)  # `command` is required
        assert result.is_error is True
        assert "Invalid arguments" in result.content

    def test_disabled_tool_is_rejected_before_parsing(self, tool_ctx):
        tool = make_read_tool(is_enabled=lambda: False)
        result = execute_tool(tool, {"nonsense": True}, tool_ctx)
        assert result.is_error is True
        assert "not available" in result.content

    def test_validate_input_rejection(self, tool_ctx):
        tool = make_read_tool(
            validate_input=lambda args, ctx: ValidationResult.invalid("no good")
        )
        result = execute_tool(tool, {}, tool_ctx)
        assert result.is_error is True
        assert result.content == "no good"

    def test_check_permissions_denial(self, tool_ctx):
        tool = make_read_tool(
            check_permissions=lambda args, ctx: PermissionResult.deny("not allowed")
        )
        result = execute_tool(tool, {}, tool_ctx)
        assert result.is_error is True
        assert result.content == "not allowed"

    def test_check_permissions_can_rewrite_input(self, tool_ctx):
        tool = make_read_tool(
            check_permissions=lambda args, ctx: PermissionResult(
                updated_input=EchoArgs(text="rewritten")
            )
        )
        assert execute_tool(tool, {"text": "original"}, tool_ctx).content == "rewritten"


class TestCapabilityGate:
    """`permission_mode` gates *what*, independently of the allowlist."""

    def test_read_only_agent_may_read(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        assert execute_tool(make_read_tool(), {}, ctx).is_error is False

    def test_read_only_agent_may_not_write(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        result = execute_tool(writer, {}, ctx)
        assert result.is_error is True
        assert "permission_mode 'read_only'" in result.content

    def test_workspace_write_agent_may_write_but_not_execute(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="workspace_write")
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        runner = make_tool("Bash", required_capability=lambda args: Capability.EXECUTE)
        assert execute_tool(writer, {}, ctx).is_error is False
        assert execute_tool(runner, {}, ctx).is_error is True

    def test_full_agent_may_execute(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="full")
        runner = make_tool("Bash", required_capability=lambda args: Capability.EXECUTE)
        assert execute_tool(runner, {}, ctx).is_error is False

    def test_per_input_capability_lets_read_only_agent_hold_bash(self, make_tool_ctx):
        """
        The researcher is `read_only` and holds `Bash`. A read-only command must pass and
        anything else must not — which only a per-input predicate can express.
        """
        ctx = make_tool_ctx(permission_mode="read_only")
        bash = make_tool(
            "Bash",
            args_schema=CommandArgs,
            call=lambda args, ctx_: ToolResult(tool_name="Bash", content=args.command),
            required_capability=lambda args: (
                Capability.READ if args.command.startswith("ls") else Capability.EXECUTE
            ),
            is_read_only=lambda args: args.command.startswith("ls"),
        )
        assert execute_tool(bash, {"command": "ls -la"}, ctx).is_error is False
        assert execute_tool(bash, {"command": "pip install x"}, ctx).is_error is True


class TestWorkspaceGate:
    """`workspace` gates *where*, orthogonally to `permission_mode`."""

    def test_unpinned_tool_runs_anywhere(self, make_tool_ctx):
        for spec in ("sandbox", "local", "both"):
            ctx = make_tool_ctx(workspace_spec=spec)
            assert execute_tool(make_read_tool(), {}, ctx).is_error is False

    def test_local_pinned_tool_rejected_for_sandbox_agent(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="sandbox")
        host_read = make_read_tool("HostRead", required_workspace=lambda args: "local")
        result = execute_tool(host_read, {}, ctx)
        assert result.is_error is True
        assert "workspace 'sandbox'" in result.content

    def test_local_pinned_tool_allowed_for_local_agent(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="local")
        host_read = make_read_tool("HostRead", required_workspace=lambda args: "local")
        assert execute_tool(host_read, {}, ctx).is_error is False

    def test_both_satisfies_either_pin(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="both")
        for pin in ("local", "sandbox"):
            tool = make_read_tool("Pinned", required_workspace=lambda args, p=pin: p)
            assert execute_tool(tool, {}, ctx).is_error is False

    def test_gates_are_orthogonal(self, make_tool_ctx):
        """A read_only + local agent reads host files and can do nothing else with them."""
        ctx = make_tool_ctx(permission_mode="read_only", workspace_spec="local")
        reader = make_read_tool("HostRead", required_workspace=lambda args: "local")
        writer = make_tool(
            "WriteFile",
            required_capability=lambda args: Capability.WRITE,
            required_workspace=lambda args: "local",
        )
        assert execute_tool(reader, {}, ctx).is_error is False
        assert execute_tool(writer, {}, ctx).is_error is True


class TestErrorContainment:
    def test_workspace_error_becomes_an_error_result(self, tool_ctx):
        def _boom(args, ctx):
            raise WorkspaceError("sandbox is gone")

        result = execute_tool(make_read_tool(call=_boom), {}, tool_ctx)
        assert result.is_error is True
        assert "sandbox is gone" in result.content

    def test_unexpected_exception_becomes_an_error_result(self, tool_ctx):
        """A tool bug must not take down its whole batch."""
        def _boom(args, ctx):
            raise ZeroDivisionError("oops")

        result = execute_tool(make_read_tool(call=_boom), {}, tool_ctx)
        assert result.is_error is True
        assert "unexpected error" in result.content
        assert "oops" in result.content

    def test_exit_code_travels_back_as_data(self, tool_ctx):
        """A non-zero exit is something the model reasons about, not infra failure."""
        tool = make_read_tool(
            call=lambda args, ctx: ToolResult(tool_name="Bash", content="boom", exit_code=1)
        )
        result = execute_tool(tool, {}, tool_ctx)
        assert result.exit_code == 1
        assert result.is_error is False


class TestResultGovernance:
    def _big_tool(self, size: int, limit: int):
        content = "x" * size
        return make_read_tool(
            "Bash",
            max_result_chars=limit,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content=content),
        )

    def test_under_limit_is_untouched(self, tool_ctx):
        result = execute_tool(self._big_tool(100, 1000), {}, tool_ctx)
        assert result.truncated is False
        assert result.persisted_path is None
        assert len(result.content) == 100

    def test_at_limit_is_untouched(self, tool_ctx):
        result = execute_tool(self._big_tool(1000, 1000), {}, tool_ctx)
        assert result.truncated is False

    def test_over_limit_is_persisted_and_previewed(self, tool_ctx, fake_sandbox):
        result = execute_tool(self._big_tool(50_000, 30_000), {}, tool_ctx)
        assert result.truncated is True
        assert result.persisted_path is not None
        assert result.persisted_path.startswith(Config.TOOL_RESULTS_DIR)
        assert fake_sandbox.files[result.persisted_path] == "x" * 50_000
        assert "characters omitted" in result.content
        assert result.persisted_path in result.content

    def test_persisted_path_is_under_the_excluded_directory(self, tool_ctx):
        """Tooling scratch must never reach the ledger or workspace_output_*."""
        result = execute_tool(self._big_tool(50_000, 30_000), {}, tool_ctx)
        assert ".tddagents/" in Config.SYNC_EXCLUDE_FALLBACK[0]
        assert result.persisted_path is not None
        assert result.persisted_path.startswith(".tddagents/")

    def test_preview_is_far_smaller_than_the_original(self, tool_ctx):
        result = execute_tool(self._big_tool(50_000, 30_000), {}, tool_ctx)
        assert len(result.content) < 5_000

    def test_unbounded_tool_is_never_persisted(self, tool_ctx, fake_sandbox):
        """ReadFile self-bounds; persisting it would create a Read -> file -> Read loop."""
        tool = self._big_tool(500_000, UNBOUNDED_RESULT_CHARS)
        result = execute_tool(tool, {}, tool_ctx)
        assert result.truncated is False
        assert result.persisted_path is None
        assert fake_sandbox.files == {}

    def test_each_persisted_result_gets_a_distinct_path(self, tool_ctx):
        tool = self._big_tool(50_000, 30_000)
        first = execute_tool(tool, {}, tool_ctx)
        second = execute_tool(tool, {}, tool_ctx)
        assert first.persisted_path != second.persisted_path

    def test_failure_to_persist_degrades_to_truncation(self, make_tool_ctx, make_fake_workspace):
        """Losing the result entirely would be worse than truncating it."""
        sandbox = make_fake_workspace()
        sandbox.fail_write_on = {"__all__"}

        def _always_fail(path, content):
            raise WorkspaceError("disk full")

        sandbox.write_file = _always_fail
        ctx = make_tool_ctx(workspace=sandbox)
        result = execute_tool(self._big_tool(50_000, 30_000), {}, ctx)
        assert result.truncated is True
        assert result.persisted_path is None
        assert len(result.content) == 30_000


class TestSyncCheckpoint:
    class _Engine:
        def __init__(self, discovered: dict[str, str] | None = None, fail: bool = False):
            self.calls: list[dict[str, str]] = []
            self.discovered = discovered or {}
            self.fail = fail

        def reconcile_ledger(self, ledger):
            self.calls.append(dict(ledger))
            if self.fail:
                raise WorkspaceError("sandbox unreachable")
            return {**ledger, **self.discovered}, object()

    def test_fires_after_a_write(self, make_tool_ctx):
        engine = self._Engine(discovered={"made_by_bash.py": "x"})
        ctx = make_tool_ctx(sync_engine=engine)
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)

        execute_tool(writer, {}, ctx)

        assert len(engine.calls) == 1
        assert ctx.ledger == {"made_by_bash.py": "x"}

    def test_does_not_fire_after_a_read(self, make_tool_ctx):
        engine = self._Engine()
        ctx = make_tool_ctx(sync_engine=engine)
        execute_tool(make_read_tool(), {}, ctx)
        assert engine.calls == []

    def test_no_engine_is_not_an_error(self, make_tool_ctx):
        ctx = make_tool_ctx(sync_engine=None)
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        assert execute_tool(writer, {}, ctx).is_error is False

    def test_reconciliation_failure_does_not_fail_the_call(self, make_tool_ctx):
        """The write landed; a sync hiccup must not report it as a failure to the model."""
        ctx = make_tool_ctx(sync_engine=self._Engine(fail=True))
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        assert execute_tool(writer, {}, ctx).is_error is False

    def test_does_not_fire_when_the_gate_rejected_the_call(self, make_tool_ctx):
        engine = self._Engine()
        ctx = make_tool_ctx(permission_mode="read_only", sync_engine=engine)
        writer = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        execute_tool(writer, {}, ctx)
        assert engine.calls == []


class TestToolResultRendering:
    def test_plain_result_renders_as_its_content(self):
        assert ToolResult(tool_name="X", content="body").rendered() == "body"

    def test_hook_feedback_is_labelled(self):
        rendered = ToolResult(tool_name="X", content="body", additional_context="careful").rendered()
        assert "body" in rendered
        assert "<hook-feedback>" in rendered
        assert "careful" in rendered


class TestCancelToken:
    def test_starts_uncancelled(self):
        assert CancelToken().cancelled is False

    def test_cancel_sets_the_flag(self):
        token = CancelToken()
        token.cancel()
        assert token.cancelled is True


class TestToolContext:
    def test_result_ids_increment(self, tool_ctx):
        assert [tool_ctx.next_result_id() for _ in range(3)] == [1, 2, 3]

    def test_contexts_do_not_share_mutable_state(self, make_tool_ctx):
        first, second = make_tool_ctx(), make_tool_ctx()
        first.ledger["a"] = "1"
        first.todos.append({"x": "y"})
        assert second.ledger == {}
        assert second.todos == []


class TestResultIdentity:
    """Every result must name the tool that produced it, on the error paths too."""

    def test_success_names_the_tool(self, tool_ctx):
        assert execute_tool(make_read_tool("Grep"), {}, tool_ctx).tool_name == "Grep"

    def test_schema_error_names_the_tool(self, tool_ctx):
        tool = make_tool("Bash", args_schema=CommandArgs)
        assert execute_tool(tool, {}, tool_ctx).tool_name == "Bash"

    def test_disabled_error_names_the_tool(self, tool_ctx):
        tool = make_read_tool("Grep", is_enabled=lambda: False)
        assert execute_tool(tool, {}, tool_ctx).tool_name == "Grep"

    def test_validation_error_names_the_tool(self, tool_ctx):
        tool = make_read_tool("Grep", validate_input=lambda a, c: ValidationResult.invalid("no"))
        assert execute_tool(tool, {}, tool_ctx).tool_name == "Grep"

    def test_permission_denial_names_the_tool(self, tool_ctx):
        tool = make_read_tool("Grep", check_permissions=lambda a, c: PermissionResult.deny("no"))
        assert execute_tool(tool, {}, tool_ctx).tool_name == "Grep"

    def test_capability_denial_names_the_tool(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        tool = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        assert execute_tool(tool, {}, ctx).tool_name == "WriteFile"

    def test_workspace_denial_names_the_tool(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="sandbox")
        tool = make_read_tool("HostRead", required_workspace=lambda args: "local")
        assert execute_tool(tool, {}, ctx).tool_name == "HostRead"

    def test_crash_names_the_tool(self, tool_ctx):
        def _boom(args, ctx):
            raise RuntimeError("x")

        assert execute_tool(make_read_tool("Grep", call=_boom), {}, tool_ctx).tool_name == "Grep"

    def test_denial_messages_name_the_tool(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        tool = make_tool("WriteFile", required_capability=lambda args: Capability.WRITE)
        assert "WriteFile" in execute_tool(tool, {}, ctx).content


class TestPreviewContent:
    """
    The preview's whole purpose is to show the beginning and the end of the real output.
    Asserting only its length would pass on a preview that showed neither.
    """

    def _overflowing(self, body: str, limit: int):
        return make_read_tool(
            "Bash",
            max_result_chars=limit,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content=body),
        )

    def test_preview_opens_with_the_real_head(self, tool_ctx):
        body = "HEAD" + ("m" * 50_000) + "TAIL"
        result = execute_tool(self._overflowing(body, 1_000), {}, tool_ctx)
        assert result.content.startswith("HEAD")

    def test_preview_closes_with_the_real_tail(self, tool_ctx):
        body = "HEAD" + ("m" * 50_000) + "TAIL"
        result = execute_tool(self._overflowing(body, 1_000), {}, tool_ctx)
        assert result.content.endswith("TAIL")

    def test_head_and_tail_are_the_configured_sizes(self, tool_ctx):
        body = "".join(str(i % 10) for i in range(50_000))
        result = execute_tool(self._overflowing(body, 1_000), {}, tool_ctx)
        assert body[:PREVIEW_HEAD_CHARS] in result.content
        assert body[-PREVIEW_TAIL_CHARS:] in result.content

    def test_omitted_count_is_exact(self, tool_ctx):
        size = 50_000
        result = execute_tool(self._overflowing("z" * size, 1_000), {}, tool_ctx)
        expected = size - PREVIEW_HEAD_CHARS - PREVIEW_TAIL_CHARS
        assert f"{expected} characters omitted" in result.content

    def test_preview_points_at_the_saved_file(self, tool_ctx):
        result = execute_tool(self._overflowing("z" * 50_000, 1_000), {}, tool_ctx)
        assert result.persisted_path is not None
        assert "ReadFile" in result.content
        assert result.persisted_path in result.content

    def test_the_saved_file_holds_the_whole_output(self, tool_ctx, fake_sandbox):
        body = "HEAD" + ("m" * 50_000) + "TAIL"
        result = execute_tool(self._overflowing(body, 1_000), {}, tool_ctx)
        assert fake_sandbox.files[result.persisted_path] == body

    def test_degraded_truncation_keeps_the_head(self, make_tool_ctx, make_fake_workspace):
        sandbox = make_fake_workspace()

        def _always_fail(path, content):
            raise WorkspaceError("disk full")

        sandbox.write_file = _always_fail
        body = "HEAD" + ("m" * 50_000)
        result = execute_tool(self._overflowing(body, 1_000), {}, make_tool_ctx(workspace=sandbox))
        assert result.content == body[:1_000]


class TestArgumentsReachThePredicates:
    """
    Each predicate must be handed the parsed arguments, not a placeholder. A fixture whose
    lambdas ignore their argument would let a wrong-argument bug pass unnoticed.
    """

    def test_validate_input_receives_the_parsed_args(self, tool_ctx):
        seen: list = []

        def _validate(args, ctx):
            seen.append(args)
            return ValidationResult()

        tool = make_read_tool(validate_input=_validate)
        execute_tool(tool, {"text": "payload"}, tool_ctx)
        assert seen[0].text == "payload"

    def test_check_permissions_receives_the_parsed_args(self, tool_ctx):
        seen: list = []

        def _check(args, ctx):
            seen.append(args)
            return PermissionResult()

        tool = make_read_tool(check_permissions=_check)
        execute_tool(tool, {"text": "payload"}, tool_ctx)
        assert seen[0].text == "payload"

    def test_required_capability_receives_the_parsed_args(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        tool = make_tool(
            "Bash",
            required_capability=lambda args: (
                Capability.READ if args.text == "safe" else Capability.EXECUTE
            ),
            is_read_only=lambda args: args.text == "safe",
        )
        assert execute_tool(tool, {"text": "safe"}, ctx).is_error is False
        assert execute_tool(tool, {"text": "risky"}, ctx).is_error is True

    def test_required_workspace_receives_the_parsed_args(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="sandbox")
        tool = make_read_tool(
            "Dual",
            required_workspace=lambda args: "local" if args.text == "host" else None,
        )
        assert execute_tool(tool, {"text": "sandbox"}, ctx).is_error is False
        assert execute_tool(tool, {"text": "host"}, ctx).is_error is True

    def test_is_read_only_receives_the_parsed_args(self, make_tool_ctx):
        engine = TestSyncCheckpoint._Engine()
        ctx = make_tool_ctx(sync_engine=engine)
        tool = make_tool(
            "Bash",
            is_read_only=lambda args: args.text == "read",
            required_capability=lambda args: Capability.READ,
        )
        execute_tool(tool, {"text": "read"}, ctx)
        assert engine.calls == []
        execute_tool(tool, {"text": "write"}, ctx)
        assert len(engine.calls) == 1

    def test_call_receives_the_parsed_args_and_the_context(self, tool_ctx):
        seen = {}

        def _call(args, ctx):
            seen["args"], seen["ctx"] = args, ctx
            return ToolResult(tool_name="Echo", content="ok")

        execute_tool(make_read_tool(call=_call), {"text": "payload"}, tool_ctx)
        assert seen["args"].text == "payload"
        assert seen["ctx"] is tool_ctx


class TestDescription:
    """`description(args)` is the compact log line, distinct from the model-facing prompt."""

    def test_description_is_input_aware(self):
        tool = make_tool("Bash", description=lambda args: f"Run {args.text}")
        assert tool.description(EchoArgs(text="ls")) == "Run ls"

    def test_description_defaults_to_the_tool_name(self):
        from app.tools.base import build_tool

        tool = build_tool(
            name="Grep",
            args_schema=EchoArgs,
            prompt="long text",
            call=lambda args, ctx: ToolResult(tool_name="Grep", content=""),
        )
        assert tool.description(EchoArgs()) == "Grep"

    def test_description_and_prompt_are_separate(self):
        tool = make_tool("Grep", description=lambda args: "short")
        assert tool.description(EchoArgs()) == "short"
        assert tool.prompt() == "The Grep tool."

    def test_unknown_override_message_lists_every_bad_name(self):
        with pytest.raises(TypeError) as caught:
            make_tool(is_readonly=lambda a: True, max_chars=5)
        assert "is_readonly, max_chars" in str(caught.value)


class TestErrorPathsAreStampedToo:
    """
    Every result must carry its position, failures included — otherwise the executor's
    reassembly puts an error in the wrong slot and the model reads it against the wrong
    call.
    """

    def _stamped(self, tool, args, ctx):
        return execute_tool(tool, args, ctx, call_index=7, call_id="xyz")

    def test_schema_error(self, tool_ctx):
        result = self._stamped(make_tool("Bash", args_schema=CommandArgs), {}, tool_ctx)
        assert (result.call_index, result.call_id) == (7, "xyz")

    def test_disabled_tool(self, tool_ctx):
        result = self._stamped(make_read_tool(is_enabled=lambda: False), {}, tool_ctx)
        assert (result.call_index, result.call_id) == (7, "xyz")

    def test_validation_error(self, tool_ctx):
        tool = make_read_tool(validate_input=lambda a, c: ValidationResult.invalid("no"))
        assert self._stamped(tool, {}, tool_ctx).call_index == 7

    def test_permission_denial(self, tool_ctx):
        tool = make_read_tool(check_permissions=lambda a, c: PermissionResult.deny("no"))
        assert self._stamped(tool, {}, tool_ctx).call_index == 7

    def test_capability_denial(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        tool = make_tool("WriteFile", required_capability=lambda a: Capability.WRITE)
        assert self._stamped(tool, {}, ctx).call_index == 7

    def test_workspace_denial(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="sandbox")
        tool = make_read_tool("HostRead", required_workspace=lambda a: "local")
        assert self._stamped(tool, {}, ctx).call_index == 7

    def test_crash(self, tool_ctx):
        def _boom(args, ctx):
            raise RuntimeError("x")

        assert self._stamped(make_read_tool(call=_boom), {}, tool_ctx).call_index == 7

    def test_workspace_error_names_the_tool_and_is_stamped(self, tool_ctx):
        def _boom(args, ctx):
            raise WorkspaceError("gone")

        result = self._stamped(make_read_tool("Grep", call=_boom), {}, tool_ctx)
        assert result.tool_name == "Grep"
        assert result.call_index == 7


class TestHookPayloadWiring:
    """
    The exact payload `execute_tool` hands the dispatcher. These key names are a public
    contract: a hook script reads them by name, so renaming one silently breaks every
    hook anyone has written.
    """

    class _Recorder:
        def __init__(self, outcome=None):
            self.calls = []
            self._outcome = outcome

        def run(self, event, **kwargs):
            from app.hooks.dispatcher import HookOutcome

            self.calls.append({"event": event, **kwargs})
            return self._outcome or HookOutcome()

    def _bash(self):
        return make_tool(
            "Bash",
            args_schema=CommandArgs,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content="out"),
            required_capability=lambda args: Capability.READ,
        )

    def test_both_events_fire_once_each(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "ls"}, ctx)
        assert [c["event"] for c in recorder.calls] == [
            HookEvent.PRE_TOOL_USE,
            HookEvent.POST_TOOL_USE,
        ]

    def test_tool_name_and_input_are_passed(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "ls -la"}, ctx)
        assert recorder.calls[0]["tool_name"] == "Bash"
        assert recorder.calls[0]["tool_input"] == {"command": "ls -la"}

    def test_command_is_extracted_for_the_if_condition(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "ls -la"}, ctx)
        assert recorder.calls[0]["command"] == "ls -la"

    def test_a_tool_without_a_command_passes_none(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(make_read_tool("Grep"), {"text": "x"}, ctx)
        assert recorder.calls[0]["command"] is None

    def test_context_fields_carry_the_exact_key_names(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(
            hook_dispatcher=recorder,
            session_id="sess-1",
            workdir="/work",
            permission_mode="full",
            agent_id="agent-9",
            agent_type="developer",
        )
        execute_tool(self._bash(), {"command": "ls"}, ctx)

        assert recorder.calls[0]["ctx_fields"] == {
            "session_id": "sess-1",
            "cwd": "/work",
            "permission_mode": "full",
            "agent_id": "agent-9",
            "agent_type": "developer",
        }

    def test_post_event_carries_the_tool_response(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "ls"}, ctx)
        assert recorder.calls[1]["tool_response"] == "out"

    def test_pre_event_carries_no_tool_response(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "ls"}, ctx)
        assert recorder.calls[0].get("tool_response") is None

    def test_post_event_sees_the_rewritten_input(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        recorder = self._Recorder(HookOutcome(updated_input={"command": "rewritten"}))
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        execute_tool(self._bash(), {"command": "original"}, ctx)
        assert recorder.calls[1]["tool_input"] == {"command": "rewritten"}


class TestAdditionalContextComposition:
    class _Fixed:
        def __init__(self, pre, post):
            self._pre, self._post = pre, post

        def run(self, event, **kwargs):
            return self._pre if event is HookEvent.PRE_TOOL_USE else self._post

    def _ctx_with(self, make_tool_ctx, pre, post):
        return make_tool_ctx(hook_dispatcher=self._Fixed(pre, post))

    def test_pre_only(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        ctx = self._ctx_with(make_tool_ctx, HookOutcome(additional_context="A"), HookOutcome())
        assert execute_tool(make_read_tool(), {}, ctx).additional_context == "A"

    def test_post_only(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        ctx = self._ctx_with(make_tool_ctx, HookOutcome(), HookOutcome(additional_context="B"))
        assert execute_tool(make_read_tool(), {}, ctx).additional_context == "B"

    def test_both_are_newline_joined_in_order(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        ctx = self._ctx_with(
            make_tool_ctx,
            HookOutcome(additional_context="A"),
            HookOutcome(additional_context="B"),
        )
        assert execute_tool(make_read_tool(), {}, ctx).additional_context == "A\nB"

    def test_a_post_denial_reason_is_appended_after_the_contexts(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        ctx = self._ctx_with(
            make_tool_ctx,
            HookOutcome(additional_context="A"),
            HookOutcome(additional_context="B", denied=True, reason="R"),
        )
        result = execute_tool(make_read_tool(), {}, ctx)
        assert result.additional_context == "A\nB\nR"
        assert result.hook_stopped_continuation is True

    def test_neither_leaves_it_empty(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        ctx = self._ctx_with(make_tool_ctx, HookOutcome(), HookOutcome())
        assert execute_tool(make_read_tool(), {}, ctx).additional_context == ""


class TestContextReachesThePredicates:
    """
    `ctx` must reach every predicate, not just `call`. A permission check that cannot see
    the permission mode is not a permission check.
    """

    def test_validate_input_receives_the_context(self, make_tool_ctx):
        ctx = make_tool_ctx(agent_id="agent-7")
        tool = make_read_tool(
            validate_input=lambda args, c: ValidationResult()
            if c is not None and c.agent_id == "agent-7"
            else ValidationResult.invalid("no context")
        )
        assert execute_tool(tool, {}, ctx).is_error is False

    def test_check_permissions_receives_the_context(self, make_tool_ctx):
        ctx = make_tool_ctx(agent_id="agent-7")
        tool = make_read_tool(
            check_permissions=lambda args, c: PermissionResult()
            if c is not None and c.agent_id == "agent-7"
            else PermissionResult.deny("no context")
        )
        assert execute_tool(tool, {}, ctx).is_error is False


class TestEveryErrorPathCarriesTheCallId:
    """The index alone is not enough — the provider matches results to calls by id."""

    def _run(self, tool, args, ctx):
        return execute_tool(tool, args, ctx, call_index=4, call_id="the-id")

    def test_validation_error(self, tool_ctx):
        tool = make_read_tool(validate_input=lambda a, c: ValidationResult.invalid("no"))
        assert self._run(tool, {}, tool_ctx).call_id == "the-id"

    def test_permission_denial(self, tool_ctx):
        tool = make_read_tool(check_permissions=lambda a, c: PermissionResult.deny("no"))
        assert self._run(tool, {}, tool_ctx).call_id == "the-id"

    def test_capability_denial(self, make_tool_ctx):
        ctx = make_tool_ctx(permission_mode="read_only")
        tool = make_tool("WriteFile", required_capability=lambda a: Capability.WRITE)
        assert self._run(tool, {}, ctx).call_id == "the-id"

    def test_workspace_denial(self, make_tool_ctx):
        ctx = make_tool_ctx(workspace_spec="sandbox")
        tool = make_read_tool("HostRead", required_workspace=lambda a: "local")
        assert self._run(tool, {}, ctx).call_id == "the-id"

    def test_hook_veto(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        class _Deny:
            def run(self, event, **kwargs):
                return HookOutcome(denied=True, reason="nope")

        ctx = make_tool_ctx(hook_dispatcher=_Deny())
        result = self._run(make_read_tool("Grep"), {}, ctx)
        assert result.call_id == "the-id"
        assert result.call_index == 4
        assert result.tool_name == "Grep"

    def test_hook_returned_bad_updated_input(self, make_tool_ctx):
        from app.hooks.dispatcher import HookOutcome

        class _Rewrite:
            def run(self, event, **kwargs):
                return HookOutcome(updated_input={"text": {"bad": "shape"}})

        ctx = make_tool_ctx(hook_dispatcher=_Rewrite())
        result = self._run(make_read_tool("Grep"), {"text": "x"}, ctx)
        assert result.is_error is True
        assert result.tool_name == "Grep"
        assert result.call_id == "the-id"


class TestPostHookPayloadIsCompleteToo:
    """The post event gets the same fields as the pre event, not a reduced version."""

    class _Recorder:
        def __init__(self):
            self.calls = []

        def run(self, event, **kwargs):
            from app.hooks.dispatcher import HookOutcome

            self.calls.append({"event": event, **kwargs})
            return HookOutcome()

    def _bash(self):
        return make_tool(
            "Bash",
            args_schema=CommandArgs,
            call=lambda args, ctx: ToolResult(tool_name="Bash", content="out"),
            required_capability=lambda args: Capability.READ,
        )

    def test_post_carries_tool_name_command_and_context(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder, session_id="s9", agent_type="tester")
        execute_tool(self._bash(), {"command": "ls -la"}, ctx)

        post = recorder.calls[1]
        assert post["tool_name"] == "Bash"
        assert post["command"] == "ls -la"
        assert post["ctx_fields"]["session_id"] == "s9"
        assert post["ctx_fields"]["agent_type"] == "tester"

    def test_both_events_receive_identical_context_fields(self, make_tool_ctx):
        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder, session_id="s9")
        execute_tool(self._bash(), {"command": "ls"}, ctx)
        assert recorder.calls[0]["ctx_fields"] == recorder.calls[1]["ctx_fields"]


class TestHookPayloadIsJsonSerializable:
    """
    `model_dump(mode="json")`, not the default python mode. The payload is written to a
    hook's stdin as JSON, so a field that only has a Python representation — an enum, a
    datetime, a Path — has to be converted before it gets there, not after.
    """

    class _Recorder:
        def __init__(self):
            self.calls = []

        def run(self, event, **kwargs):
            from app.hooks.dispatcher import HookOutcome

            self.calls.append(kwargs)
            return HookOutcome()

    def test_enum_arguments_are_dumped_as_their_values(self, make_tool_ctx):
        import json
        from enum import Enum

        from pydantic import BaseModel as PydanticBase

        class Mode(str, Enum):
            FAST = "fast"

        class Args(PydanticBase):
            mode: Mode = Mode.FAST

        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        tool = make_tool(
            "Modal",
            args_schema=Args,
            call=lambda args, c: ToolResult(tool_name="Modal", content="ok"),
            required_capability=lambda args: Capability.READ,
        )
        execute_tool(tool, {"mode": "fast"}, ctx)

        payload = recorder.calls[0]["tool_input"]
        assert payload == {"mode": "fast"}
        assert json.dumps(payload)  # must survive the trip to a hook's stdin

    def test_datetime_arguments_are_dumped_as_strings(self, make_tool_ctx):
        import json
        from datetime import datetime

        from pydantic import BaseModel as PydanticBase

        class Args(PydanticBase):
            when: datetime

        recorder = self._Recorder()
        ctx = make_tool_ctx(hook_dispatcher=recorder)
        tool = make_tool(
            "Timed",
            args_schema=Args,
            call=lambda args, c: ToolResult(tool_name="Timed", content="ok"),
            required_capability=lambda args: Capability.READ,
        )
        execute_tool(tool, {"when": "2026-08-23T10:00:00"}, ctx)

        payload = recorder.calls[0]["tool_input"]
        assert isinstance(payload["when"], str)
        assert json.dumps(payload)
