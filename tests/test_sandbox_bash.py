"""
The Bash tool in the sandbox: `shouldUseSandbox` (`app/tools/bash_tool/should_use_sandbox.py`), the
prompt's "Command sandbox" section, `LocalWorkspace`'s one wrap point, and Bash end to end through a
fake `bwrap` on `PATH` (the wrapped command really runs, the cwd file lives in the sandbox's temp
directory, `dangerouslyDisableSandbox` and `excludedCommands` run unwrapped, and TDDAgents' own
internals — Grep's ripgrep — are never wrapped).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.tools.bash_tool import bash_tool as bash_mod
from app.tools.bash_tool.bash_tool import build_bash_tool, get_bash_prompt, get_simple_sandbox_section
from app.tools.bash_tool.should_use_sandbox import contains_excluded_command, should_use_sandbox
from app.utils.sandbox.runtime.network_host import HostNetworkContext
from app.utils.sandbox.runtime.sandbox_schemas import (
    FsReadRestrictionConfig,
    FsWriteRestrictionConfig,
    NetworkRestrictionConfig,
)
from app.utils.sandbox.sandbox_adapter import SandboxAdapter
from app.utils.settings.settings import SettingsLocations
from app.workspace.base import WorkspaceProviderError
from app.workspace.local import LocalWorkspace
from tests.sandbox_doubles import bwrap_calls, install_fake_sandbox_tools


@dataclass
class StubSandbox:
    """What Bash reads of the run's sandbox."""

    enabled: bool = True
    unsandboxed_allowed: bool = True
    excluded: list[str] = field(default_factory=list)
    temp_dir: str = "/tmp/tddagents-1000"
    wrapped: list[tuple[str, str | None]] = field(default_factory=list)
    cleanups: int = 0
    wrap_error: Exception | None = None

    def is_sandboxing_enabled(self) -> bool:
        return self.enabled

    def are_unsandboxed_commands_allowed(self) -> bool:
        return self.unsandboxed_allowed

    def get_excluded_commands(self) -> list[str]:
        return self.excluded

    def wrap_with_sandbox(self, command: str, bin_shell: str | None = None) -> str:
        if self.wrap_error is not None:
            raise self.wrap_error
        self.wrapped.append((command, bin_shell))
        return f"echo wrapped && {command}"

    def cleanup_after_command(self) -> None:
        self.cleanups += 1

    def annotate_stderr_with_sandbox_failures(self, command: str, stderr: str) -> str:
        return stderr

    # The prompt section's reads.
    def get_fs_read_config(self) -> FsReadRestrictionConfig:
        return FsReadRestrictionConfig(deny_only=["~", "~"], allow_within_deny=["."])

    def get_fs_write_config(self) -> FsWriteRestrictionConfig:
        return FsWriteRestrictionConfig(allow_only=["/dev/null", self.temp_dir, ".", self.temp_dir],
                                        deny_within_allow=["/p/.tddagents/project.json"])

    def get_network_restriction_config(self) -> NetworkRestrictionConfig:
        return NetworkRestrictionConfig(allowed_hosts=["pypi.org", "pypi.org"], denied_hosts=["evil.io"])

    def get_allow_unix_sockets(self) -> list[str] | None:
        return None

    def get_ignore_violations(self) -> dict[str, list[str]] | None:
        return None


# ── shouldUseSandbox ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("args, sandbox, expected", [
    ({"command": "ls"}, None, False),
    ({"command": "ls"}, StubSandbox(enabled=False), False),
    ({"command": "ls"}, StubSandbox(), True),
    ({"command": ""}, StubSandbox(), False),
    ({"command": "ls", "dangerouslyDisableSandbox": True}, StubSandbox(), False),
    ({"command": "ls", "dangerouslyDisableSandbox": True}, StubSandbox(unsandboxed_allowed=False), True),
    ({"command": "docker ps"}, StubSandbox(excluded=["docker:*"]), False),
    ({"command": "npm test"}, StubSandbox(excluded=["docker:*"]), True),
])
def test_should_use_sandbox(args: dict[str, Any], sandbox: Any, expected: bool) -> None:
    assert should_use_sandbox(args, sandbox) is expected


@pytest.mark.parametrize("command, patterns, excluded", [
    ("docker ps", ["docker:*"], True),
    ("docker", ["docker:*"], True),
    ("dockerd run", ["docker:*"], False),
    ("make build", ["make build"], True),
    ("make build -j2", ["make build"], False),
    ("npm run test:unit", ["npm run *"], True),
    ("ls && docker ps", ["docker:*"], True),
    ("FOO=1 bazel run x", ["bazel:*"], True),
    ("timeout 30 bazel run x", ["bazel:*"], True),
    ("timeout 300 FOO=bar bazel run", ["bazel:*"], True),
    ("PATH=/evil bazel run", ["bazel:*"], False),
    ("ls", [], False),
])
def test_excluded_commands(command: str, patterns: list[str], excluded: bool) -> None:
    assert contains_excluded_command(command, patterns) is excluded


# ── the prompt section ─────────────────────────────────────────────────────────


def test_no_sandbox_leaves_the_prompt_as_it_was() -> None:
    prompt = get_bash_prompt()
    assert "{{" not in prompt and "## Command sandbox" not in prompt
    assert "rewrite the command without them.\n\n# Committing changes with git" in prompt
    assert get_simple_sandbox_section(StubSandbox(enabled=False)) == ""


def test_the_sandbox_section_is_upstreams() -> None:
    section = get_simple_sandbox_section(StubSandbox())
    assert section.split("\n")[:6] == [
        "",
        "## Command sandbox",
        "By default, your command will be run in a sandbox. This sandbox controls which directories and network "
        "hosts commands may access or modify without an explicit override.",
        "",
        "The sandbox has the following restrictions:",
        'Filesystem: {"read":{"denyOnly":["~"],"allowWithinDeny":["."]},"write":{"allowOnly":["/dev/null",'
        '"$TMPDIR","."],"denyWithinAllow":["/p/.tddagents/project.json"]}}',
    ]
    lines = section.split("\n")
    assert lines[6] == 'Network: {"allowedHosts":["pypi.org"],"deniedHosts":["evil.io"]}'
    assert lines[7] == ""
    assert lines[8] == (" - You should always default to running commands within the sandbox. Do NOT attempt to set "
                        "`dangerouslyDisableSandbox: true` unless:")
    assert lines[9] == "  - The user *explicitly* asks you to bypass sandbox"
    assert lines[-1] == (" - For temporary files, always use the `$TMPDIR` environment variable. TMPDIR is "
                         "automatically set to the correct sandbox-writable directory in sandbox mode. Do NOT use "
                         "`/tmp` directly - use `$TMPDIR` instead.")
    forbidden = get_simple_sandbox_section(StubSandbox(unsandboxed_allowed=False)).split("\n")
    assert forbidden[7:11] == [
        "",
        " - All commands MUST run in sandbox mode - the `dangerouslyDisableSandbox` parameter is disabled by policy.",
        " - Commands cannot run outside the sandbox under any circumstances.",
        " - If a command fails due to sandbox restrictions, work with the user to adjust sandbox settings instead."]


def test_unix_sockets_and_ignored_violations_join_the_section() -> None:
    class Extra(StubSandbox):
        def get_allow_unix_sockets(self) -> list[str] | None:
            return []

        def get_ignore_violations(self) -> dict[str, list[str]] | None:
            return {"*": ["/x"]}

        def get_network_restriction_config(self) -> NetworkRestrictionConfig:
            return NetworkRestrictionConfig()

    lines = get_simple_sandbox_section(Extra()).split("\n")
    assert lines[6] == 'Network: {"allowUnixSockets":[]}'
    assert lines[7] == 'Ignored violations: {"*":["/x"]}'


def test_the_prompt_places_the_section_before_the_git_instructions() -> None:
    prompt = get_bash_prompt(StubSandbox())
    before, _, after = prompt.partition("\n## Command sandbox\n")
    assert before.endswith("rewrite the command without them.\n")
    assert "use `$TMPDIR` instead.\n\n# Committing changes with git" in after
    assert build_bash_tool(StubSandbox()).prompt == prompt


# ── the wrap point ──────────────────────────────────────────────────────────────


def test_unsandboxed_commands_run_in_a_login_shell_and_sandboxed_ones_wrapped(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "p")
    assert ws._argv("ls", False) == ["/bin/bash", "-l", "-c", "ls"]
    with pytest.raises(WorkspaceProviderError, match="no sandbox is attached"):
        ws._argv("ls", True)
    with pytest.raises(WorkspaceProviderError, match="no sandbox is attached"):
        ws.execute("ls", sandbox=True)
    sandbox = StubSandbox(temp_dir=str(tmp_path / "sbx-tmp"))
    ws.sandbox_manager = sandbox
    assert ws._argv("ls", True) == ["/bin/bash", "-c", "echo wrapped && ls"]
    assert sandbox.wrapped == [("ls", "/bin/bash")]
    sandbox.wrap_error = RuntimeError("Sandbox failed to initialize. ")
    with pytest.raises(WorkspaceProviderError, match="Could not sandbox the command: Sandbox failed to initialize"):
        ws._argv("ls", True)


def test_a_sandboxed_execute_gets_the_temp_dir_and_cleans_up(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "p")
    sandbox = StubSandbox(temp_dir=str(tmp_path / "sbx-tmp"))
    ws.sandbox_manager = sandbox
    result = ws.execute('echo "$TMPDIR|$TDDAGENTS_TMPDIR|$TMPPREFIX"', sandbox=True)
    tmp = str(tmp_path / "sbx-tmp")
    assert result.stdout == f"wrapped\n{tmp}|{tmp}|{tmp}/zsh\n"
    assert (os.stat(tmp).st_mode & 0o777) == 0o700 and sandbox.cleanups == 1
    plain = ws.execute("echo plain")
    assert plain.stdout == "plain\n" and sandbox.cleanups == 1 and len(sandbox.wrapped) == 1


def test_a_sandboxed_spawn_cleans_up_once_it_exits(tmp_path: Path) -> None:
    ws = LocalWorkspace(tmp_path / "p")
    sandbox = StubSandbox(temp_dir=str(tmp_path / "sbx-tmp"))
    ws.sandbox_manager = sandbox
    out = tmp_path / "out"

    async def scenario() -> int:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT)
        try:
            process = await ws.spawn("echo hi", output_fd=fd, sandbox=True)
        finally:
            os.close(fd)
        code = await process.wait()
        await process.wait()
        return code

    assert asyncio.run(scenario()) == 0
    assert out.read_text() == "wrapped\nhi\n" and sandbox.cleanups == 1


# ── Bash end to end, through a fake bwrap ───────────────────────────────────────


@pytest.fixture
def sandboxed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A project whose settings enable the sandbox, a started adapter over it, and fake bwrap / socat."""
    log = install_fake_sandbox_tools(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BWRAP_LOG", str(log))
    project = tmp_path / "project"
    (project / ".tddagents").mkdir(parents=True)
    (project / ".tddagents" / "settings.json").write_text(json.dumps({"sandbox": {
        "enabled": True, "excludedCommands": ["docker:*"], "network": {"allowedDomains": ["pypi.org"]}}}))
    locations = SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=tmp_path / "etc")

    def no_network(_filter: Any, **_kw: Any) -> HostNetworkContext:
        return HostNetworkContext(http_proxy_port=1, socks_proxy_port=2)

    adapter = SandboxAdapter(locations, start_network=no_network, temp_dir=str(tmp_path / "sbx-tmp"))
    adapter.initialize()
    workspace = LocalWorkspace(project)
    workspace.sandbox_manager = adapter
    ctx = tool_context_for(AppStateStore(), workspace=workspace)
    ctx.sandbox_manager = adapter
    yield {"ctx": ctx, "log": log, "project": project, "adapter": adapter, "tmp": tmp_path}
    adapter.reset()


def _bash(ctx: Any, **args: Any) -> Any:
    return asyncio.run(build_bash_tool().call(args, ctx))


def _wrapped(log: Path) -> list[list[str]]:
    return [call for call in bwrap_calls(log) if call[:2] == ["--new-session", "--die-with-parent"]]


def test_bash_runs_the_command_inside_bwrap(sandboxed: dict[str, Any]) -> None:
    result = _bash(sandboxed["ctx"], command="echo inside > made.txt && cat made.txt")
    assert result.content == "inside" and not result.is_error
    assert (sandboxed["project"] / "made.txt").read_text() == "inside\n"
    calls = _wrapped(sandboxed["log"])
    assert len(calls) == 1
    argv = calls[0]
    # No bridge in this run: the namespace is closed outright, and the project is bound writable.
    project = str(sandboxed["project"])
    binds = [argv[i:i + 3] for i, arg in enumerate(argv) if arg == "--bind"]
    assert "--unshare-net" in argv and ["--bind", project, project] in binds
    assert argv[argv.index("--") + 1:argv.index("--") + 3] == ["/bin/bash", "-c"]


def test_the_cwd_file_lives_in_the_sandbox_temp_dir_and_cd_is_tracked(sandboxed: dict[str, Any]) -> None:
    from app.utils.cwd import get_cwd

    (sandboxed["project"] / "sub").mkdir()
    _bash(sandboxed["ctx"], command="cd sub")
    assert get_cwd(sandboxed["ctx"]) == str((sandboxed["project"] / "sub").resolve())
    inner = _wrapped(sandboxed["log"])[0][-1]
    assert f"pwd -P >| {sandboxed['tmp'] / 'sbx-tmp'}/cwd-" in inner
    assert not list((sandboxed["tmp"] / "sbx-tmp").glob("cwd-*")), "the cwd file is removed once read"


def test_dangerously_disable_sandbox_and_excluded_commands_run_unwrapped(sandboxed: dict[str, Any]) -> None:
    result = _bash(sandboxed["ctx"], command="echo free", dangerouslyDisableSandbox=True)
    assert result.content == "free" and result.metadata["dangerouslyDisableSandbox"] is True
    _bash(sandboxed["ctx"], command="docker --version || true")
    assert _wrapped(sandboxed["log"]) == []


def test_internals_are_never_wrapped(sandboxed: dict[str, Any]) -> None:
    from app.tools.grep_tool.grep_tool import build_grep_tool

    (sandboxed["project"] / "a.txt").write_text("needle\n")
    result = asyncio.run(build_grep_tool().call({"pattern": "needle"}, sandboxed["ctx"]))
    assert "a.txt" in str(result.content)
    sandboxed["ctx"].workspace.execute("true")
    # Only the adapter's one-off probe ever ran bwrap.
    assert bwrap_calls(sandboxed["log"]) == [["--ro-bind", "/", "/", "--unshare-net", "true"]]


def test_a_failing_wrap_is_an_execution_error(sandboxed: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kw: Any) -> str:
        raise RuntimeError("Linux HTTP bridge socket does not exist")

    monkeypatch.setattr(sandboxed["adapter"], "wrap_with_sandbox", broken)
    result = _bash(sandboxed["ctx"], command="echo never")
    assert result.is_error and "Could not sandbox the command: Linux HTTP bridge socket does not exist" in str(
        result.content)


def test_the_output_is_annotated_with_the_sandbox_violations(sandboxed: dict[str, Any],
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandboxed["adapter"], "annotate_stderr_with_sandbox_failures",
                        lambda command, stderr: f"{stderr}\n<sandbox_violations>\nx\n</sandbox_violations>")
    result = _bash(sandboxed["ctx"], command="echo out")
    # The output ends with its newline, then the annotation's own (upstream's `EOL + '<sandbox_violations>'`).
    assert result.content == "out\n\n<sandbox_violations>\nx\n</sandbox_violations>"


def test_the_bash_module_passes_the_run_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    async def recording(context: Any, command: str, **kw: Any) -> Any:
        seen.append(kw["should_use_sandbox"])
        raise RuntimeError("stop here")

    monkeypatch.setattr(bash_mod, "exec_shell", recording)
    ctx = tool_context_for(AppStateStore(), workspace=LocalWorkspace("/tmp"))
    for sandbox in (None, StubSandbox()):
        ctx.sandbox_manager = sandbox
        with pytest.raises(RuntimeError, match="stop here"):
            asyncio.run(build_bash_tool().call({"command": "ls"}, ctx))
    assert seen == [False, True]
