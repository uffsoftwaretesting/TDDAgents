"""
`app/loop/tools/ripgrep.py`, the port of claude-code's `src/utils/ripgrep.ts`.

A scripted workspace stands in for the shell so each upstream branch — exit 0/1, critical
exit, EAGAIN retry, timeout, partial output, first-use probe, sandbox provisioning — is
exercised exactly; one test runs the real vendored binary.
"""

import shlex
import sysconfig
from pathlib import Path

import pytest

from app.loop.tools import ripgrep as rg
from app.loop.tools.ripgrep import (
    RIPGREP_PIP_SPEC,
    SANDBOX_RIPGREP_DIR,
    RipgrepTimeoutError,
    RipgrepUnavailableError,
    ensure_ripgrep,
    provision_sandbox_ripgrep,
    rip_grep,
    ripgrep_command,
    to_relative_path,
)
from app.workspace.base import CommandResult, WorkspaceTimeout
from app.workspace.local import LocalWorkspace

SANDBOX_RG = f"{SANDBOX_RIPGREP_DIR}/bin/rg"


def result(stdout="", stderr="", code=0):
    return CommandResult(stdout=stdout, stderr=stderr, exit_code=code, duration=0.0, workspace="sandbox")


class Scripted:
    """Answers commands from a list of (predicate, reply) rules; logs (cmd, timeout)."""

    def __init__(self, kind="sandbox", version_ok=True):
        self.kind = kind
        self.log: list[tuple[str, float | None]] = []
        self.replies: list = []
        self.version_ok = version_ok
        self.installed = False

    def execute(self, cmd, timeout=None, env=None):
        self.log.append((cmd, timeout))
        if cmd.endswith("--version"):
            ok = self.version_ok or self.installed
            return result("ripgrep 15.1.0\n" if ok else "", code=0 if ok else 127)
        if "pip install" in cmd:
            self.installed = True
            return result()
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def builtin_mode(monkeypatch):
    monkeypatch.delenv("USE_BUILTIN_RIPGREP", raising=False)


def test_constants():
    assert RIPGREP_PIP_SPEC == "ripgrep==15.1.0"
    assert SANDBOX_RIPGREP_DIR == "/tmp/tddagents-ripgrep"
    assert rg.RIPGREP_TIMEOUT_SECONDS == 20
    assert rg.VERSION_PROBE_TIMEOUT_SECONDS == 5
    assert rg.CRITICAL_EXIT_CODES == (126, 127)


def test_pin_matches_requirements():
    reqs = (Path(__file__).parent.parent / "requirements.txt").read_text().split()
    assert RIPGREP_PIP_SPEC in reqs


@pytest.mark.parametrize("value, falsy", [
    (None, False), ("", False), ("1", False), ("true", False),
    ("0", True), ("false", True), (" No ", True), ("OFF", True),
])
def test_is_env_defined_falsy(value, falsy):
    assert rg.is_env_defined_falsy(value) is falsy


def test_command_resolution(monkeypatch):
    assert ripgrep_command(Scripted(kind="local")) == str(Path(sysconfig.get_path("scripts")) / "rg")
    assert ripgrep_command(object()) == rg.builtin_ripgrep_path()  # no kind: treated as host
    assert ripgrep_command(Scripted(kind="sandbox")) == SANDBOX_RG
    monkeypatch.setenv("USE_BUILTIN_RIPGREP", "0")
    assert ripgrep_command(Scripted(kind="sandbox")) == "rg"
    assert ripgrep_command(Scripted(kind="local")) == "rg"


def test_provision_command():
    assert shlex.split(rg.provision_command()) == [
        "python3", "-m", "pip", "install", "--quiet", "--target", SANDBOX_RIPGREP_DIR, RIPGREP_PIP_SPEC]


def test_provision_failure_raises():
    class Fails(Scripted):
        def execute(self, cmd, timeout=None, env=None):
            return result(stderr=" no network \n", code=1)

    expected = r"^Could not install ripgrep==15.1.0 in the sandbox: no network$"
    with pytest.raises(RipgrepUnavailableError, match=expected):
        provision_sandbox_ripgrep(Fails())


def test_ensure_probes_once_per_workspace():
    ws = Scripted()
    ensure_ripgrep(ws)
    ensure_ripgrep(ws)
    assert ws.log == [(f"{SANDBOX_RG} --version", 5)]


def test_ensure_provisions_sandbox_when_probe_fails():
    ws = Scripted(version_ok=False)
    ensure_ripgrep(ws)
    assert [c for c, _ in ws.log] == [f"{SANDBOX_RG} --version", rg.provision_command(), f"{SANDBOX_RG} --version"]


def test_ensure_does_not_provision_host_and_raises():
    ws = Scripted(kind="local", version_ok=False)
    with pytest.raises(RipgrepUnavailableError, match="ripgrep is not available at "):
        ensure_ripgrep(ws)
    assert len(ws.log) == 1
    with pytest.raises(RipgrepUnavailableError):
        ensure_ripgrep(ws)  # failures are not memoized
    assert len(ws.log) == 2


def test_ensure_raises_if_still_broken_after_provisioning():
    class NeverWorks(Scripted):
        def execute(self, cmd, timeout=None, env=None):
            self.log.append((cmd, timeout))
            return result(code=0 if "pip" in cmd else 127)

    ws = NeverWorks()
    with pytest.raises(RipgrepUnavailableError, match=f"ripgrep is not available at {SANDBOX_RG}"):
        ensure_ripgrep(ws)
    assert len(ws.log) == 3


def test_probe_requires_ripgrep_banner_and_survives_exceptions():
    class Banner(Scripted):
        def __init__(self, reply):
            super().__init__(kind="local")
            self.reply = reply

        def execute(self, cmd, timeout=None, env=None):
            if isinstance(self.reply, BaseException):
                raise self.reply
            return self.reply

    assert rg._probe(Banner(result("ripgrep 1.0")), "rg") is True
    assert rg._probe(Banner(result("grep 1.0")), "rg") is False
    assert rg._probe(Banner(result("ripgrep 1.0", code=2)), "rg") is False
    assert rg._probe(Banner(RuntimeError("x")), "rg") is False


def test_ensure_works_for_unhashable_weakref_targets():
    class Slotted:
        __slots__ = ("kind", "calls")

        def __init__(self):
            self.kind = "local"
            self.calls = 0

        def execute(self, cmd, timeout=None, env=None):
            self.calls += 1
            return result("ripgrep 15.1.0")

    ws = Slotted()
    ensure_ripgrep(ws)
    ensure_ripgrep(ws)
    assert ws.calls == 2  # not weak-referenceable: probed every time, never crashes


def test_rip_grep_success_runs_argv_with_timeout():
    ws = Scripted()
    ws.replies = [result("./a\n")]
    assert rip_grep(ws, ["--files"], ".") == ["./a"]
    cmd, timeout = ws.log[-1]
    assert shlex.split(cmd) == [SANDBOX_RG, "--files", "."]
    assert timeout == 20


def test_rip_grep_line_cleanup():
    ws = Scripted()
    ws.replies = [result("\n./a\r\n\r\nb\n")]
    assert rip_grep(ws, [], ".") == ["./a", "b"]


def test_rip_grep_exit_1_is_no_matches():
    ws = Scripted()
    ws.replies = [result("ignored", code=1)]
    assert rip_grep(ws, ["x"], ".") == []


@pytest.mark.parametrize("code", [126, 127])
def test_rip_grep_critical_exit_raises(code):
    ws = Scripted()
    ws.replies = [result(stderr=" denied ", code=code)]
    with pytest.raises(RipgrepUnavailableError, match=rf"^ripgrep could not be executed \(exit {code}\): denied$"):
        rip_grep(ws, ["x"], ".")


@pytest.mark.parametrize("stderr", ["os error 11", "Resource temporarily unavailable"])
def test_rip_grep_eagain_retries_once_single_threaded(stderr):
    ws = Scripted()
    ws.replies = [result(stderr=stderr, code=2), result("ok\n")]
    assert rip_grep(ws, ["x"], "src") == ["ok"]
    assert shlex.split(ws.log[-1][0]) == [SANDBOX_RG, "-j", "1", "x", "src"]


def test_rip_grep_eagain_twice_returns_partial_output():
    ws = Scripted()
    ws.replies = [result(stderr="os error 11", code=2), result("part\n", stderr="os error 11", code=2)]
    assert rip_grep(ws, ["x"], ".") == ["part"]
    assert len([c for c, _ in ws.log if not c.endswith("--version")]) == 2


def test_rip_grep_other_error_returns_partial_output():
    ws = Scripted()
    ws.replies = [result("a\nb\n", stderr="regex parse error", code=2)]
    assert rip_grep(ws, ["("], ".") == ["a", "b"]


def test_rip_grep_timeout_raises_upstream_message():
    ws = Scripted()
    ws.replies = [WorkspaceTimeout("slow")]
    with pytest.raises(RipgrepTimeoutError) as info:
        rip_grep(ws, ["x"], ".")
    assert str(info.value) == (
        "Ripgrep search timed out after 20 seconds. The search may have matched files but did "
        "not complete in time. Try searching a more specific path or pattern.")


def test_rip_grep_quotes_arguments():
    ws = Scripted()
    ws.replies = [result()]
    rip_grep(ws, ["--glob", "*.py", "a b; rm -rf /"], "my dir")
    assert shlex.split(ws.log[-1][0]) == [SANDBOX_RG, "--glob", "*.py", "a b; rm -rf /", "my dir"]


def test_to_relative_path():
    assert to_relative_path("./a/b") == "a/b"
    assert to_relative_path("././a") == "a"
    assert to_relative_path("a/./b") == "a/./b"
    assert to_relative_path(".hidden") == ".hidden"


def test_real_vendored_binary(tmp_path):
    (tmp_path / "x.py").write_text("needle\n")
    ws = LocalWorkspace(str(tmp_path))
    assert rip_grep(ws, ["--files"], ".") == ["./x.py"]
    assert rip_grep(ws, ["-l", "needle"], ".") == ["./x.py"]
    assert rip_grep(ws, ["-l", "absent"], ".") == []


def test_rip_grep_line_cleanup_keeps_whitespace_and_x_lines():
    ws = Scripted()
    ws.replies = [result("a  \r\n  \nX\nb")]
    assert rip_grep(ws, [], ".") == ["a  ", "  ", "X", "b"]
