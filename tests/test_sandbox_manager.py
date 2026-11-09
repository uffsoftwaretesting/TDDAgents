"""
`SandboxManager` (`app/utils/sandbox/runtime/sandbox_manager.py`): domain matching, the network filter,
initialization and reset, the configs it derives, and the wrap it builds; and the socat bridge
(`linux_network_bridge.py`) against a fake `socat` on `PATH`.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.utils.sandbox.runtime import linux_network_bridge as bridge
from app.utils.sandbox.runtime.network_host import HostNetworkContext
from app.utils.sandbox.runtime.sandbox_config import (
    FilesystemConfig,
    MitmProxyConfig,
    NetworkConfig,
    RipgrepConfig,
    SandboxRuntimeConfig,
    SeccompConfig,
)
from app.utils.sandbox.runtime.sandbox_manager import SandboxDependencyError, SandboxManager, matches_domain_pattern
from app.utils.sandbox.runtime.sandbox_violation_store import SandboxViolationEvent
from tests.sandbox_doubles import FakeFs, RecordingRipgrep

HOME, CWD, TMP = "/home/u", "/home/u/ws/p", "/tmp/tddagents-1000"


def config(**network: Any) -> SandboxRuntimeConfig:
    return SandboxRuntimeConfig(
        network=NetworkConfig(allowed_domains=network.pop("allowed", ["pypi.org", "*.npmjs.org"]),
                              denied_domains=network.pop("denied", ["bad.pypi.org"]), **network),
        filesystem=FilesystemConfig(deny_read=["~"], allow_read=["."], allow_write=["."],
                                    deny_write=[f"{CWD}/.tddagents/project.json"]))


class FakeNetwork:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.filter: Any = None
        self.closed = 0

    def __call__(self, filter_request: Any, **kwargs: Any) -> HostNetworkContext:
        self.filter = filter_request
        self.calls.append(kwargs)
        manager_ref = self

        class Context(HostNetworkContext):
            def close(self) -> None:
                manager_ref.closed += 1

        return Context(http_proxy_port=41001, socks_proxy_port=41002)


def manager(fs: FakeFs | None = None, network: FakeNetwork | None = None, **kw: Any) -> SandboxManager:
    return SandboxManager(cwd=CWD, home=HOME, tmpdir=TMP, platform=kw.pop("platform", "linux"),
                          which=kw.pop("which", lambda name: f"/usr/bin/{name}" if not name.startswith("/") else name),
                          fs=fs or FakeFs(dirs={CWD, TMP}), run_ripgrep=RecordingRipgrep(),
                          start_network=network or FakeNetwork(), **kw)


@pytest.mark.parametrize("host, pattern, matches", [
    ("pypi.org", "pypi.org", True), ("PyPI.org", "pypi.ORG", True), ("files.pypi.org", "pypi.org", False),
    ("registry.npmjs.org", "*.npmjs.org", True), ("npmjs.org", "*.npmjs.org", False),
    ("a.b.npmjs.org", "*.NPMJS.org", True), ("evilnpmjs.org", "*.npmjs.org", False),
])
def test_domain_matching(host: str, pattern: str, matches: bool) -> None:
    assert matches_domain_pattern(host, pattern) is matches


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_without_a_config_every_request_is_denied() -> None:
    assert run(manager().filter_network_request(443, "pypi.org")) is False


def test_the_filter_checks_denies_then_allows_then_asks() -> None:
    asked: list[Any] = []

    async def ask(pattern: Any) -> bool:
        asked.append(dict(pattern))
        return bool(pattern["host"] == "ok.example.com")

    m = manager()
    m.initialize(config())
    assert run(m.filter_network_request(443, "bad.pypi.org", ask)) is False
    assert run(m.filter_network_request(443, "pypi.org", ask)) is True
    assert run(m.filter_network_request(443, "registry.npmjs.org", ask)) is True
    assert asked == []
    assert run(m.filter_network_request(443, "ok.example.com", ask)) is True
    assert run(m.filter_network_request(80, "no.example.com", ask)) is False
    assert asked == [{"host": "ok.example.com", "port": 443}, {"host": "no.example.com", "port": 80}]
    assert run(m.filter_network_request(443, "other.example.com")) is False


def test_a_failing_ask_denies() -> None:
    async def ask(_pattern: Any) -> bool:
        raise RuntimeError("dialog closed")

    m = manager()
    m.initialize(config())
    assert run(m.filter_network_request(443, "unknown.example.com", ask)) is False


def test_initialize_starts_the_network_with_the_filter_and_the_ask() -> None:
    network = FakeNetwork()
    asked: list[str] = []

    async def ask(pattern: Any) -> bool:
        asked.append(pattern["host"])
        return True

    m = manager(network=network)
    assert not m.is_sandboxing_enabled() and not m.wait_for_network_initialization()
    m.initialize(config(http_proxy_port=9001), ask)
    assert m.is_sandboxing_enabled() and m.wait_for_network_initialization()
    assert network.calls == [{"get_mitm_socket_path": m.get_mitm_socket_path, "http_proxy_port": 9001,
                              "socks_proxy_port": None, "linux": True}]
    assert run(network.filter(443, "new.example.com")) is True and asked == ["new.example.com"]
    assert (m.get_proxy_port(), m.get_socks_proxy_port()) == (41001, 41002)
    m.initialize(config())
    assert len(network.calls) == 1, "a second initialize does nothing"
    m.reset()
    assert network.closed == 1 and m.get_proxy_port() is None and not m.wait_for_network_initialization()
    m.reset()
    assert network.closed == 1


def test_initialize_refuses_when_a_dependency_is_missing() -> None:
    network = FakeNetwork()
    m = manager(network=network, which=lambda name: None if name == "socat" else f"/usr/bin/{name}")
    with pytest.raises(SandboxDependencyError, match="Sandbox dependencies not available: socat not installed"):
        m.initialize(config())
    assert network.calls == []


def test_dependencies_and_platforms() -> None:
    assert manager().check_dependencies().errors == []
    missing_rg = manager(which=lambda name: None if name == "/opt/rg" else f"/usr/bin/{name}")
    assert missing_rg.check_dependencies(RipgrepConfig(command="/opt/rg")).errors == ["ripgrep (/opt/rg) not found"]
    assert manager(platform="macos").check_dependencies().errors == ["Unsupported platform"]
    assert manager(platform="linux", wsl_version="1").is_supported_platform() is False
    assert manager(platform="linux", wsl_version="2").is_supported_platform() is True
    assert manager(platform="windows").is_supported_platform() is False


def test_the_mitm_socket_is_returned_for_matching_hosts_only() -> None:
    m = manager()
    assert m.get_mitm_socket_path("api.corp.io") is None
    m.initialize(config(mitm_proxy=MitmProxyConfig(socket_path="/m.sock", domains=["*.corp.io"])))
    assert m.get_mitm_socket_path("api.corp.io") == "/m.sock"
    assert m.get_mitm_socket_path("corp.io") is None


def test_the_derived_configs() -> None:
    fs = FakeFs(dirs={CWD, TMP}, files={f"{CWD}/a.env", f"{CWD}/b.env"})
    m = manager(fs)
    assert m.get_fs_read_config().deny_only == [] and m.get_network_restriction_config().allowed_hosts is None
    assert m.get_fs_write_config().allow_only[6] == TMP
    cfg = replace(config(), filesystem=FilesystemConfig(
        deny_read=["~", "*.env"], allow_read=["./src/**"], allow_write=[".", "build/**", "x/*/y"],
        deny_write=["secret/**", "*.key"]))
    m.initialize(cfg)
    # `~` stays as written (the argv builder normalizes it); a glob is expanded here.
    assert m.get_fs_read_config().deny_only == ["~", f"{CWD}/a.env", f"{CWD}/b.env"]
    assert m.get_fs_read_config().allow_within_deny == ["./src"]
    write = m.get_fs_write_config()
    assert write.allow_only[-2:] == [".", "build"] and write.deny_within_allow == ["secret"]
    assert m.get_linux_glob_pattern_warnings() == ["x/*/y", "*.key"]
    restriction = m.get_network_restriction_config()
    assert restriction.allowed_hosts == ["pypi.org", "*.npmjs.org"] and restriction.denied_hosts == ["bad.pypi.org"]
    assert manager(platform="macos").get_linux_glob_pattern_warnings() == []


def test_the_getters_follow_the_config() -> None:
    m = manager()
    assert m.get_ripgrep_config() == RipgrepConfig(command="rg") and m.get_mandatory_deny_search_depth() == 3
    assert not m.get_allow_git_config() and m.get_seccomp_config() is None
    assert m.get_linux_http_socket_path() is None and m.get_linux_socks_socket_path() is None
    cfg = replace(config(allow_unix_sockets=["/s"], allow_all_unix_sockets=True, allow_local_binding=True),
                  ignore_violations={"*": []}, enable_weaker_nested_sandbox=True, mandatory_deny_search_depth=5,
                  ripgrep=RipgrepConfig(command="/x/rg"), seccomp=SeccompConfig(bpf_path="/b"),
                  filesystem=FilesystemConfig(deny_read=[], allow_write=[], deny_write=[], allow_git_config=True))
    m.initialize(cfg)
    assert (m.get_allow_unix_sockets(), m.get_allow_all_unix_sockets(), m.get_allow_local_binding()) == (
        ["/s"], True, True)
    assert m.get_ignore_violations() == {"*": []} and m.get_enable_weaker_nested_sandbox() is True
    assert m.get_mandatory_deny_search_depth() == 5 and m.get_ripgrep_config().command == "/x/rg"
    assert m.get_allow_git_config() and m.get_seccomp_config() == SeccompConfig(bpf_path="/b")
    assert m.get_config() is cfg


def test_update_config_takes_a_copy() -> None:
    m = manager()
    m.initialize(config())
    new = config(allowed=["example.com"])
    m.update_config(new)
    new.network.allowed_domains.append("leak.com")
    current = m.get_config()
    assert current is not None and current.network.allowed_domains == ["example.com"]
    assert run(m.filter_network_request(443, "pypi.org")) is False


def test_the_wrap_restricts_the_network_once_configured(tmp_path: Path) -> None:
    fs = FakeFs(dirs={CWD, TMP})
    m = manager(fs)
    unconfigured = shlex.split(m.wrap_with_sandbox("ls", "/bin/bash"))
    assert "--unshare-net" not in unconfigured and unconfigured[:3] == ["bwrap", "--new-session", "--die-with-parent"]
    m.initialize(config())
    argv = shlex.split(m.wrap_with_sandbox("ls", "/bin/bash"))
    # The fake network has no bridge, so the namespace is closed outright (no proxy environment).
    assert argv[3] == "--unshare-net" and "--setenv" not in argv
    assert ["--tmpfs", HOME, "--bind", CWD, CWD] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 5]
    with pytest.raises(RuntimeError, match="not supported on platform: macos"):
        manager(platform="macos").wrap_with_sandbox("ls")


def test_annotate_appends_the_commands_violations() -> None:
    from datetime import datetime

    from app.utils.sandbox.runtime.sandbox_utils import encode_sandboxed_command

    m = manager()
    assert m.annotate_stderr_with_sandbox_failures("ls", "err") == "err"
    m.initialize(config())
    assert m.annotate_stderr_with_sandbox_failures("ls", "err") == "err"
    m.get_sandbox_violation_store().add_violation(SandboxViolationEvent(
        line="deny file-write /etc", timestamp=datetime.now(), encoded_command=encode_sandboxed_command("ls")))
    assert m.annotate_stderr_with_sandbox_failures("ls", "err") == (
        f"err{os.linesep}<sandbox_violations>{os.linesep}deny file-write /etc{os.linesep}</sandbox_violations>")


def test_cleanup_after_command_removes_tracked_mount_points(tmp_path: Path) -> None:
    from app.utils.sandbox.runtime import linux_sandbox_utils as lsu

    ghost = tmp_path / ".bashrc"
    ghost.write_text("")
    lsu._bwrap_mount_points[str(ghost)] = None
    lsu._active_sandbox_count = 2
    manager().cleanup_after_command()
    assert ghost.exists(), "deferred while another wrapped command runs"
    manager().reset()
    assert not ghost.exists() and lsu.active_sandbox_count() == 0, "reset forces the cleanup"


# ── the socat bridge, against a fake socat ──────────────────────────────────────

FAKE_SOCAT = """#!/bin/sh
# Records its arguments and creates the Unix socket path it listens on, then stays up.
echo "$@" >> "$FAKE_SOCAT_LOG"
case "$1" in UNIX-LISTEN:*) p="${1#UNIX-LISTEN:}"; p="${p%%,*}"; [ -n "$FAKE_SOCAT_NO_SOCKET" ] || : > "$p";; esac
[ -n "$FAKE_SOCAT_DIE" ] && exit 3
exec tail -f /dev/null
"""


@pytest.fixture
def fake_socat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    socat = bin_dir / "socat"
    socat.write_text(FAKE_SOCAT)
    socat.chmod(socat.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "socat.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SOCAT_LOG", str(log))
    return log


def test_the_bridge_starts_two_socat_processes_and_stops_them(fake_socat: Path, tmp_path: Path) -> None:
    context = bridge.initialize_linux_network_bridge(41001, 41002, tmpdir=str(tmp_path))
    try:
        assert os.path.exists(context.http_socket_path) and os.path.exists(context.socks_socket_path)
        assert Path(context.http_socket_path).name.startswith("tddagents-http-")
        lines = fake_socat.read_text().splitlines()
        assert lines == [
            f"UNIX-LISTEN:{context.http_socket_path},fork,reuseaddr "
            "TCP:localhost:41001,keepalive,keepidle=10,keepintvl=5,keepcnt=3",
            f"UNIX-LISTEN:{context.socks_socket_path},fork,reuseaddr "
            "TCP:localhost:41002,keepalive,keepidle=10,keepintvl=5,keepcnt=3"]
        assert context.http_bridge_process.poll() is None and context.socks_bridge_process.poll() is None
    finally:
        bridge.shutdown_linux_network_bridge(context)
    assert context.http_bridge_process.poll() is not None and context.socks_bridge_process.poll() is not None
    assert not os.path.exists(context.http_socket_path) and not os.path.exists(context.socks_socket_path)
    bridge.shutdown_linux_network_bridge(context)


def test_a_bridge_whose_sockets_never_appear_fails(fake_socat: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SOCAT_NO_SOCKET", "1")
    with pytest.raises(bridge.BridgeError, match="Failed to create bridge sockets after 5 attempts"):
        bridge.initialize_linux_network_bridge(1, 2, tmpdir=str(tmp_path))


def test_a_bridge_process_that_dies_fails(fake_socat: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SOCAT_NO_SOCKET", "1")
    monkeypatch.setenv("FAKE_SOCAT_DIE", "1")

    def slow_exists(_path: str) -> bool:
        import time

        time.sleep(0.2)
        return False

    monkeypatch.setattr(os.path, "exists", slow_exists)
    with pytest.raises(bridge.BridgeError, match="Linux bridge process died unexpectedly"):
        bridge.initialize_linux_network_bridge(1, 2, tmpdir=str(tmp_path))


def test_a_missing_socat_cannot_start_the_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(bridge.BridgeError, match="Failed to start HTTP bridge process"):
        bridge.initialize_linux_network_bridge(1, 2, tmpdir=str(tmp_path))


def test_the_real_network_host_starts_proxies_and_the_bridge(fake_socat: Path) -> None:
    m = SandboxManager(cwd=CWD, home=HOME, tmpdir=TMP, platform="linux", wsl_version=None,
                       which=lambda name: f"/usr/bin/{name}")
    m.initialize(config())
    try:
        http_path, socks_path = m.get_linux_http_socket_path(), m.get_linux_socks_socket_path()
        assert http_path and socks_path and os.path.exists(http_path)
        port = m.get_proxy_port()
        assert port and f"TCP:localhost:{port}," in fake_socat.read_text()
    finally:
        m.reset()
    assert not os.path.exists(http_path)
