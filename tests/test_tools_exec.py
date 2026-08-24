"""
The four execution tools, and the per-input read-only parse that `Bash` is built on.

The parse is the most security-relevant function in the tool layer: it is what decides
whether a `read_only` agent may run a given command. Everything it cannot prove safe must
come back False, so most of this file is adversarial input.
"""

from __future__ import annotations

import pytest

from app.tools.base import Capability, execute_tool
from app.tools.exec import (
    Bash,
    BashOutput,
    KillShell,
    RunCode,
    is_read_only_command,
    sandbox_adapter,
)


def run(tool, args, ctx):
    return execute_tool(tool, args, ctx)


class FakeBackgroundCommand:
    def __init__(self, command_id="bash_1", cmd="sleep 1"):
        self.id = command_id
        self.cmd = cmd
        self.finished = False
        self.exit_code = None
        self._chunks: list[tuple[str, str]] = []

    def feed(self, stdout="", stderr=""):
        self._chunks.append((stdout, stderr))

    def drain(self):
        out = "".join(c[0] for c in self._chunks)
        err = "".join(c[1] for c in self._chunks)
        self._chunks.clear()
        return out, err


class FakeAdapter:
    """
    Stands in for E2BAdapter, which is the exempt seam.

    The tools are not exempt, so they are tested against this the same way
    `app/workspace/e2b.py` is tested against a fake adapter.
    """

    def __init__(self):
        self.background: dict[str, FakeBackgroundCommand] = {}
        self.killed: list[str] = []
        self.executed: list[str] = []
        self.code_runs: list[str] = []
        self.execution = None

    def start_background(self, cmd, cwd=None):
        command = FakeBackgroundCommand(f"bash_{len(self.background) + 1}", cmd)
        self.background[command.id] = command
        return command

    def get_background(self, command_id):
        return self.background.get(command_id)

    def kill_command(self, command_id):
        if command_id not in self.background:
            return False
        self.killed.append(command_id)
        del self.background[command_id]
        return True

    def run_code(self, code, language=None):
        self.code_runs.append(code)
        return self.execution


class FakeSandboxWorkspace:
    """A Workspace that also exposes `.adapter`, the way E2BWorkspace does."""

    kind = "sandbox"

    def __init__(self, adapter):
        self.adapter = adapter
        self.commands: list[str] = []

    def execute(self, cmd, timeout=None, env=None):
        from app.workspace.base import CommandResult

        self.commands.append(cmd)
        return CommandResult(stdout="ok", stderr="", exit_code=0, duration=0.0, workspace="sandbox")


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def sandbox_ctx(make_tool_ctx, adapter):
    return make_tool_ctx(workspace=FakeSandboxWorkspace(adapter))


class TestReadOnlyCommandParse:
    @pytest.mark.parametrize("command", [
        "ls", "ls -la", "cat a.py", "grep -rn x .", "find . -name '*.py'",
        "wc -l a.py", "head -n 5 a.py", "pwd", "echo hello", "which python",
        "/usr/bin/ls -l", "PYTHONPATH=. grep x a.py",
    ])
    def test_recognized_readers_are_read_only(self, command):
        assert is_read_only_command(command) is True

    @pytest.mark.parametrize("command", [
        "rm -rf /", "pip install requests", "mv a b", "touch new", "mkdir d",
        "python script.py", "npm install", "chmod +x a", "curl http://x",
    ])
    def test_writers_and_unknown_programs_are_not(self, command):
        assert is_read_only_command(command) is False

    @pytest.mark.parametrize("command", [
        "ls > out.txt", "ls; rm -rf /", "ls && rm x", "ls || rm x",
        "echo $(rm -rf /)", "cat `whoami`", "sudo ls", "ls &",
    ])
    def test_shell_metacharacters_fail_closed(self, command):
        """A redirect writes and a subshell can do anything; neither is classifiable."""
        assert is_read_only_command(command) is False

    def test_a_pipeline_is_read_only_only_if_every_stage_is(self):
        assert is_read_only_command("cat a.py | grep x | wc -l") is True
        assert is_read_only_command("cat a.py | tee out.txt") is False

    def test_unparseable_input_fails_closed(self):
        assert is_read_only_command("echo 'unterminated") is False

    @pytest.mark.parametrize("command", ["", "   "])
    def test_empty_input_fails_closed(self, command):
        assert is_read_only_command(command) is False

    def test_git_read_subcommands(self):
        assert is_read_only_command("git status") is True
        assert is_read_only_command("git log --oneline") is True
        assert is_read_only_command("git push") is False
        assert is_read_only_command("git") is False

    def test_pip_read_subcommands(self):
        assert is_read_only_command("pip list") is True
        assert is_read_only_command("pip install x") is False

    def test_python_is_never_read_only(self):
        """`python -c` runs arbitrary code, so no python invocation can be proven safe."""
        assert is_read_only_command("python -c 'print(1)'") is False
        assert is_read_only_command("python --version") is False

    def test_env_assignments_are_stripped_before_matching(self):
        assert is_read_only_command("FOO=bar BAZ=qux ls") is True
        assert is_read_only_command("FOO=bar rm x") is False

    def test_only_assignments_fails_closed(self):
        assert is_read_only_command("FOO=bar") is False


class TestBashPredicates:
    def test_read_only_command_is_parallelizable_and_needs_only_read(self):
        args = Bash.args_schema(command="ls -la")
        assert Bash.is_read_only(args) is True
        assert Bash.is_concurrency_safe(args) is True
        assert Bash.is_destructive(args) is False
        assert Bash.required_capability(args) is Capability.READ

    def test_a_writing_command_is_none_of_those(self):
        args = Bash.args_schema(command="rm -rf /")
        assert Bash.is_read_only(args) is False
        assert Bash.is_concurrency_safe(args) is False
        assert Bash.is_destructive(args) is True
        assert Bash.required_capability(args) is Capability.EXECUTE

    def test_a_background_start_is_never_a_read(self):
        """Even `ls`: starting a process is an execution whatever it runs."""
        args = Bash.args_schema(command="ls", run_in_background=True)
        assert Bash.is_read_only(args) is False
        assert Bash.required_capability(args) is Capability.EXECUTE

    def test_concurrency_safety_delegates_to_read_only(self):
        for command in ("ls", "rm x", "cat a | grep b"):
            args = Bash.args_schema(command=command)
            assert Bash.is_concurrency_safe(args) == Bash.is_read_only(args)


class TestBashExecution:
    def test_returns_stdout_and_exit_code(self, sandbox_ctx):
        result = run(Bash, {"command": "ls"}, sandbox_ctx)
        assert "ok" in result.content
        assert "exit code: 0" in result.content
        assert result.exit_code == 0

    def test_a_nonzero_exit_is_data_not_an_error(self, make_tool_ctx, fake_sandbox):
        """This is the CommandExitException reclassification, on the tool path."""
        from app.workspace.base import CommandResult

        fake_sandbox.command_results["false"] = CommandResult(
            stdout="", stderr="boom", exit_code=1, duration=0.0, workspace="sandbox"
        )
        result = run(Bash, {"command": "false"}, make_tool_ctx())
        assert result.is_error is False
        assert result.exit_code == 1
        assert "boom" in result.content

    def test_stderr_is_labelled(self, make_tool_ctx, fake_sandbox):
        from app.workspace.base import CommandResult

        fake_sandbox.command_results["x"] = CommandResult(
            stdout="out", stderr="err", exit_code=0, duration=0.0, workspace="sandbox"
        )
        content = run(Bash, {"command": "x"}, make_tool_ctx()).content
        assert "out" in content
        assert "--- stderr ---" in content
        assert "err" in content

    def test_no_output_says_so(self, make_tool_ctx):
        assert "(no output)" in run(Bash, {"command": "true"}, make_tool_ctx()).content

    def test_a_read_only_agent_may_run_a_read_only_command(self, make_tool_ctx, adapter):
        ctx = make_tool_ctx(
            workspace=FakeSandboxWorkspace(adapter), permission_mode="read_only"
        )
        assert run(Bash, {"command": "ls"}, ctx).is_error is False

    def test_a_read_only_agent_may_not_run_anything_else(self, make_tool_ctx, adapter):
        ctx = make_tool_ctx(
            workspace=FakeSandboxWorkspace(adapter), permission_mode="read_only"
        )
        result = run(Bash, {"command": "pip install requests"}, ctx)
        assert result.is_error is True
        assert "read_only" in result.content

    def test_result_limit_is_thirty_thousand(self):
        from app.tools.base import BASH_MAX_RESULT_CHARS

        assert Bash.max_result_chars == BASH_MAX_RESULT_CHARS


class TestBackgroundCommands:
    def test_starting_returns_an_id_and_instructions(self, sandbox_ctx, adapter):
        result = run(Bash, {"command": "sleep 60", "run_in_background": True}, sandbox_ctx)
        assert "bash_1" in result.content
        assert "BashOutput" in result.content
        assert "KillShell" in result.content
        assert "bash_1" in adapter.background

    def test_the_context_tracks_what_this_agent_started(self, sandbox_ctx):
        """Phase 2's teardown kills only the commands its own agent started."""
        run(Bash, {"command": "sleep 60", "run_in_background": True}, sandbox_ctx)
        assert sandbox_ctx.background_commands == {"bash_1": "sleep 60"}

    def test_reading_output_drains_it(self, sandbox_ctx, adapter):
        run(Bash, {"command": "sleep 60", "run_in_background": True}, sandbox_ctx)
        adapter.background["bash_1"].feed(stdout="first chunk")

        first = run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx)
        assert "first chunk" in first.content

        second = run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx)
        assert "No new output" in second.content

    def test_reading_an_unknown_id_is_an_error(self, sandbox_ctx):
        result = run(BashOutput, {"command_id": "nope"}, sandbox_ctx)
        assert result.is_error is True
        assert "No background command" in result.content

    def test_killing_stops_it_and_clears_the_context(self, sandbox_ctx, adapter):
        run(Bash, {"command": "sleep 60", "run_in_background": True}, sandbox_ctx)
        result = run(KillShell, {"command_id": "bash_1"}, sandbox_ctx)
        assert result.is_error is False
        assert adapter.killed == ["bash_1"]
        assert sandbox_ctx.background_commands == {}

    def test_killing_an_unknown_id_is_an_error(self, sandbox_ctx):
        assert run(KillShell, {"command_id": "nope"}, sandbox_ctx).is_error is True

    def test_both_are_sandbox_pinned(self):
        assert BashOutput.required_workspace(BashOutput.args_schema(command_id="x")) == "sandbox"
        assert KillShell.required_workspace(KillShell.args_schema(command_id="x")) == "sandbox"

    def test_a_local_agent_cannot_reach_them(self, make_tool_ctx, local_ws):
        ctx = make_tool_ctx(workspace=local_ws, workspace_spec="local")
        result = run(BashOutput, {"command_id": "x"}, ctx)
        assert result.is_error is True
        assert "workspace 'local'" in result.content

    def test_a_workspace_without_an_adapter_reports_it_clearly(self, make_tool_ctx, local_ws):
        """`workspace: both` passes the gate, so the tool itself must handle the absence."""
        ctx = make_tool_ctx(workspace=local_ws, workspace_spec="both")
        result = run(BashOutput, {"command_id": "x"}, ctx)
        assert result.is_error is True
        assert "sandbox workspace" in result.content


class TestSandboxAdapterLookup:
    def test_finds_a_directly_exposed_adapter(self, make_tool_ctx, adapter):
        ctx = make_tool_ctx(workspace=FakeSandboxWorkspace(adapter))
        assert sandbox_adapter(ctx) is adapter

    def test_finds_one_behind_a_dual_workspace(self, make_tool_ctx, adapter, local_ws):
        from app.workspace.router import DualWorkspace

        dual = DualWorkspace(FakeSandboxWorkspace(adapter), local_ws)  # type: ignore[arg-type]
        assert sandbox_adapter(make_tool_ctx(workspace=dual)) is adapter

    def test_returns_none_for_a_local_workspace(self, make_tool_ctx, local_ws):
        assert sandbox_adapter(make_tool_ctx(workspace=local_ws)) is None


class TestRunCode:
    class _Execution:
        def __init__(self, stdout=None, stderr=None, error=None, results=None):
            self.logs = type("Logs", (), {"stdout": stdout or [], "stderr": stderr or []})()
            self.error = error
            self.results = results or []

    def test_renders_stdout(self, sandbox_ctx, adapter):
        adapter.execution = self._Execution(stdout=["hello\n"])
        assert "hello" in run(RunCode, {"code": "print('hello')"}, sandbox_ctx).content

    def test_renders_stderr_with_a_label(self, sandbox_ctx, adapter):
        adapter.execution = self._Execution(stderr=["warning\n"])
        content = run(RunCode, {"code": "x"}, sandbox_ctx).content
        assert "--- stderr ---" in content
        assert "warning" in content

    def test_renders_an_error(self, sandbox_ctx, adapter):
        adapter.execution = self._Execution(
            error=type("Err", (), {"name": "ValueError", "value": "bad"})()
        )
        content = run(RunCode, {"code": "x"}, sandbox_ctx).content
        assert "ValueError: bad" in content

    def test_renders_result_text(self, sandbox_ctx, adapter):
        adapter.execution = self._Execution(results=[type("R", (), {"text": "42"})()])
        assert "42" in run(RunCode, {"code": "6*7"}, sandbox_ctx).content

    def test_empty_execution(self, sandbox_ctx, adapter):
        adapter.execution = self._Execution()
        assert "(no output)" in run(RunCode, {"code": "pass"}, sandbox_ctx).content

    def test_an_unrecognized_execution_shape_does_not_crash(self, sandbox_ctx, adapter):
        """The one place a provider object reaches a tool; read it defensively."""
        adapter.execution = object()
        assert run(RunCode, {"code": "x"}, sandbox_ctx).is_error is False

    def test_needs_execute_capability(self, make_tool_ctx, adapter):
        ctx = make_tool_ctx(
            workspace=FakeSandboxWorkspace(adapter), permission_mode="workspace_write"
        )
        result = run(RunCode, {"code": "x"}, ctx)
        assert result.is_error is True
        assert "workspace_write" in result.content

    def test_is_sandbox_pinned(self):
        assert RunCode.required_workspace(RunCode.args_schema(code="x")) == "sandbox"

    def test_a_workspace_error_becomes_a_tool_error(self, sandbox_ctx, adapter):
        from app.workspace.base import WorkspaceError

        def _boom(code, language=None):
            raise WorkspaceError("sandbox gone")

        adapter.run_code = _boom
        result = run(RunCode, {"code": "x"}, sandbox_ctx)
        assert result.is_error is True
        assert "sandbox gone" in result.content


class TestBashRequestForwarding:
    def test_the_command_reaches_the_workspace_verbatim(self, sandbox_ctx):
        run(Bash, {"command": "echo 'quoted; text'"}, sandbox_ctx)
        assert sandbox_ctx.workspace.commands == ["echo 'quoted; text'"]

    def test_an_explicit_timeout_is_forwarded(self, make_tool_ctx):
        seen = {}

        class _Recorder:
            kind = "sandbox"

            def execute(self, cmd, timeout=None, env=None):
                from app.workspace.base import CommandResult

                seen["timeout"] = timeout
                return CommandResult(stdout="", stderr="", exit_code=0, duration=0.0,
                                     workspace="sandbox")

        run(Bash, {"command": "ls", "timeout": 12.5}, make_tool_ctx(workspace=_Recorder()))
        assert seen["timeout"] == 12.5

    def test_the_command_reaches_the_adapter_for_a_background_start(self, sandbox_ctx, adapter):
        run(Bash, {"command": "sleep 99", "run_in_background": True}, sandbox_ctx)
        assert adapter.background["bash_1"].cmd == "sleep 99"


class TestCommandRendering:
    """Exact output shape — it is what the model parses to decide what happened."""

    def _result(self, make_tool_ctx, stdout, stderr, exit_code):
        from app.workspace.base import CommandResult

        class _Fixed:
            kind = "sandbox"

            def execute(self, cmd, timeout=None, env=None):
                return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code,
                                     duration=0.0, workspace="sandbox")

        return run(Bash, {"command": "x"}, make_tool_ctx(workspace=_Fixed()))

    def test_trailing_whitespace_is_stripped_leading_is_kept(self, make_tool_ctx):
        """Indentation carries meaning in program output; trailing newlines do not."""
        content = self._result(make_tool_ctx, "  indented\n\n\n", "", 0).content
        assert content.startswith("  indented")
        assert "\n\n\n" not in content

    def test_stderr_trailing_whitespace_is_stripped_too(self, make_tool_ctx):
        content = self._result(make_tool_ctx, "", "  warn\n\n", 0).content
        assert "  warn" in content
        assert content.endswith("--- exit code: 0 ---")

    def test_whitespace_only_output_counts_as_no_output(self, make_tool_ctx):
        assert "(no output)" in self._result(make_tool_ctx, "   \n", "  ", 0).content

    def test_the_exit_code_is_always_the_last_line(self, make_tool_ctx):
        content = self._result(make_tool_ctx, "out", "err", 7).content
        assert content.splitlines()[-1] == "--- exit code: 7 ---"


class TestProgramNameExtraction:
    def test_a_deeply_nested_path_still_resolves_to_the_program(self):
        """rsplit must take only the last segment, whatever the path depth."""
        assert is_read_only_command("/usr/local/bin/ls -l") is True
        assert is_read_only_command("/a/b/c/d/e/rm -rf /") is False

    def test_a_relative_path(self):
        assert is_read_only_command("./ls") is True

    def test_an_uppercase_env_assignment_is_stripped(self):
        assert is_read_only_command("MY_VAR=1 ls") is True

    def test_a_lowercase_env_assignment_is_stripped(self):
        assert is_read_only_command("my_var=1 ls") is True

    def test_a_leading_digit_is_not_an_env_assignment(self):
        assert is_read_only_command("1VAR=x ls") is False


class TestBackgroundErrorMessages:
    def test_the_no_sandbox_message_is_exact(self, make_tool_ctx, local_ws):
        ctx = make_tool_ctx(workspace=local_ws, workspace_spec="both")
        result = run(Bash, {"command": "ls", "run_in_background": True}, ctx)
        assert result.content == "Background commands require the sandbox workspace."

    def test_the_unknown_id_message_names_the_id(self, sandbox_ctx):
        result = run(BashOutput, {"command_id": "bash_99"}, sandbox_ctx)
        assert result.content == "No background command with id bash_99."

    def test_the_no_new_output_message_reports_the_state(self, sandbox_ctx, adapter):
        run(Bash, {"command": "sleep 1", "run_in_background": True}, sandbox_ctx)
        result = run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx)
        assert "still running" in result.content

        adapter.background["bash_1"].finished = True
        assert "finished" in run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx).content

    def test_kill_reports_the_id_it_stopped(self, sandbox_ctx):
        run(Bash, {"command": "sleep 1", "run_in_background": True}, sandbox_ctx)
        assert run(KillShell, {"command_id": "bash_1"}, sandbox_ctx).content == "Stopped bash_1."


class TestExactSandboxRequiredMessages:
    """
    Pinned exactly. These are the messages an agent reads when it asks for a capability its
    workspace cannot provide, and a vague one sends it round the same loop again.
    """

    def _no_adapter_ctx(self, make_tool_ctx, local_ws):
        # `both` passes the workspace gate, so the tool itself must handle the absence.
        return make_tool_ctx(workspace=local_ws, workspace_spec="both")

    def test_bash_output(self, make_tool_ctx, local_ws):
        ctx = self._no_adapter_ctx(make_tool_ctx, local_ws)
        result = run(BashOutput, {"command_id": "x"}, ctx)
        assert result.content == "Background commands require the sandbox workspace."

    def test_kill_shell(self, make_tool_ctx, local_ws):
        ctx = self._no_adapter_ctx(make_tool_ctx, local_ws)
        result = run(KillShell, {"command_id": "x"}, ctx)
        assert result.content == "Background commands require the sandbox workspace."

    def test_run_code(self, make_tool_ctx, local_ws):
        ctx = self._no_adapter_ctx(make_tool_ctx, local_ws)
        assert run(RunCode, {"code": "x"}, ctx).content == "RunCode requires the sandbox workspace."


class TestRunCodeForwarding:
    def test_the_code_reaches_the_adapter(self, sandbox_ctx, adapter):
        run(RunCode, {"code": "print(1)"}, sandbox_ctx)
        assert adapter.code_runs == ["print(1)"]

    def test_the_language_reaches_the_adapter(self, sandbox_ctx, adapter):
        seen = {}

        def _run_code(code, language=None):
            seen["language"] = language
            return None

        adapter.run_code = _run_code
        run(RunCode, {"code": "puts 1", "language": "ruby"}, sandbox_ctx)
        assert seen["language"] == "ruby"

    def test_the_default_language_is_none(self, sandbox_ctx, adapter):
        seen = {}

        def _run_code(code, language=None):
            seen["language"] = language
            return None

        adapter.run_code = _run_code
        run(RunCode, {"code": "print(1)"}, sandbox_ctx)
        assert seen["language"] is None


class TestBackgroundOutputRendering:
    def test_a_finished_command_reports_its_exit_code(self, sandbox_ctx, adapter):
        run(Bash, {"command": "x", "run_in_background": True}, sandbox_ctx)
        command = adapter.background["bash_1"]
        command.feed(stdout="done")
        command.exit_code = 5

        content = run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx).content
        assert "--- exit code: 5 ---" in content

    def test_a_still_running_command_reports_zero(self, sandbox_ctx, adapter):
        """`exit_code or 0` — a command that has not exited has no code to report yet."""
        run(Bash, {"command": "x", "run_in_background": True}, sandbox_ctx)
        adapter.background["bash_1"].feed(stdout="partial")
        content = run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx).content
        assert "--- exit code: 0 ---" in content

    def test_a_zero_exit_code_renders_as_zero(self, sandbox_ctx, adapter):
        run(Bash, {"command": "x", "run_in_background": True}, sandbox_ctx)
        command = adapter.background["bash_1"]
        command.feed(stdout="ok")
        command.exit_code = 0
        assert "--- exit code: 0 ---" in run(BashOutput, {"command_id": "bash_1"}, sandbox_ctx).content


class TestKillShellBookkeeping:
    def test_killing_a_command_this_agent_did_not_start_is_not_an_error(self, sandbox_ctx, adapter):
        """
        The adapter knows the command but this context never recorded it — `pop` must
        tolerate the absence rather than raising KeyError.
        """
        adapter.start_background("someone else's command")
        assert sandbox_ctx.background_commands == {}
        result = run(KillShell, {"command_id": "bash_1"}, sandbox_ctx)
        assert result.is_error is False
        assert result.content == "Stopped bash_1."

    def test_killing_twice_reports_the_second_as_unknown(self, sandbox_ctx):
        run(Bash, {"command": "x", "run_in_background": True}, sandbox_ctx)
        assert run(KillShell, {"command_id": "bash_1"}, sandbox_ctx).is_error is False
        assert run(KillShell, {"command_id": "bash_1"}, sandbox_ctx).is_error is True
