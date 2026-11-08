"""
`SandboxAdapter` (`app/utils/sandbox/sandbox_adapter.py`): the settings and permission rules turned into the
runtime config, the enablement rules and their texts, the settings writes, the scrub of planted bare-repo
files, and the `/doctor` section.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.components.sandbox.sandbox_doctor_section import sandbox_doctor_section
from app.types.permissions import PermissionRuleSource
from app.utils.sandbox.runtime.network_host import HostNetworkContext
from app.utils.sandbox.sandbox_adapter import (
    SandboxAdapter,
    SandboxUnavailableError,
    add_to_excluded_commands,
    detect_worktree_main_repo_path,
    permission_rule_extract_prefix,
    permission_rule_value_from_string,
    resolve_path_pattern_for_sandbox,
    resolve_sandbox_filesystem_path,
)
from app.utils.sandbox.sandbox_ui_utils import remove_sandbox_violation_tags
from app.utils.settings.settings import SettingsLocations
from tests.sandbox_doubles import RecordingRipgrep

TMP = "/tmp/tddagents-1000"


def write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "home" / "ws" / "project"
    project.mkdir(parents=True)
    return {"project": project, "home": tmp_path / "home", "managed": tmp_path / "etc",
            "flag": tmp_path / "flag.json"}


def locations(layout: dict[str, Path], *, flag: bool = False, extra: tuple[str, ...] = ()) -> SettingsLocations:
    return SettingsLocations(project_root=layout["project"], home=layout["home"], managed_dir=layout["managed"],
                             flag_settings_path=layout["flag"] if flag else None, additional_directories=extra)


class Network:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0
        self.filter: Any = None

    def __call__(self, filter_request: Any, **_kw: Any) -> HostNetworkContext:
        self.calls += 1
        self.filter = filter_request
        outer = self

        class Context(HostNetworkContext):
            def close(self) -> None:
                outer.closed += 1

        return Context(http_proxy_port=1, socks_proxy_port=2)


def adapter(layout: dict[str, Path], *, platform: str = "linux", missing: tuple[str, ...] = (),
            probe: str | None = None, network: Any = None, cwd: str | None = None,
            **kw: Any) -> SandboxAdapter:
    return SandboxAdapter(
        kw.pop("locs", None) or locations(layout), home=str(layout["home"]), platform=platform,
        which=lambda name: None if name in missing else f"/usr/bin/{name}", temp_dir=TMP,
        run_ripgrep=RecordingRipgrep(), start_network=network or Network(), ripgrep_command="/opt/rg",
        probe=lambda: probe, cwd_state=(lambda: cwd) if cwd else None, **kw)


def project_settings(layout: dict[str, Path], data: Any, name: str = "settings.json") -> None:
    write(layout["project"] / ".tddagents" / name, data)


# ── the local rule parsing and path resolution ──────────────────────────────────


def test_the_local_rule_parsers() -> None:
    assert permission_rule_value_from_string("WebFetch(domain:x.io)") == ("WebFetch", "domain:x.io")
    assert permission_rule_value_from_string("Bash") == ("Bash", None)
    assert permission_rule_value_from_string("Edit()") == ("Edit()", None)
    assert permission_rule_extract_prefix("npm run test:*") == "npm run test"
    assert permission_rule_extract_prefix("npm test") is None


def test_rule_paths_and_filesystem_paths_resolve_differently(layout: dict[str, Path]) -> None:
    locs = locations(layout, flag=True)
    project, user = PermissionRuleSource.PROJECT_SETTINGS, PermissionRuleSource.USER_SETTINGS
    assert resolve_path_pattern_for_sandbox("//etc/x", project, locs) == "/etc/x"
    assert resolve_path_pattern_for_sandbox("/build/**", project, locs) == f"{layout['project']}/build/**"
    assert resolve_path_pattern_for_sandbox("/x", user, locs) == f"{layout['home']}/.tddagents/x"
    assert resolve_path_pattern_for_sandbox("/x", PermissionRuleSource.FLAG_SETTINGS, locs) == (
        f"{layout['flag'].parent}/x")
    assert resolve_path_pattern_for_sandbox("~/x", project, locs) == "~/x"
    assert resolve_path_pattern_for_sandbox("src", project, locs) == "src"
    assert resolve_sandbox_filesystem_path("/opt/cache", project, locs) == "/opt/cache"
    assert resolve_sandbox_filesystem_path("//opt/cache", project, locs) == "/opt/cache"
    assert resolve_sandbox_filesystem_path("cache", project, locs) == f"{layout['project']}/cache"
    assert resolve_sandbox_filesystem_path("~/.cache", project, locs).endswith("/.cache")


# ── convertToSandboxRuntimeConfig ───────────────────────────────────────────────


def test_the_runtime_config_from_every_source(layout: dict[str, Path]) -> None:
    project = layout["project"]
    (project / ".git").mkdir()
    (project / "HEAD").write_text("planted")
    project_settings(layout, {
        "permissions": {"allow": ["WebFetch(domain:docs.python.org)", "Edit(/gen/**)", "Bash(ls)"],
                        "deny": ["WebFetch(domain:evil.io)", "Edit(/secret)", "Read(//etc/shadow)"],
                        "additionalDirectories": ["/data/shared"]},
        "sandbox": {"enabled": True, "network": {"allowedDomains": ["pypi.org"], "allowLocalBinding": True,
                                                 "allowUnixSockets": ["/var/run/x.sock"], "httpProxyPort": 3000},
                    "filesystem": {"denyRead": ["~"], "allowRead": ["."], "denyWrite": [".tddagents/project.json"],
                                   "allowWrite": ["/opt/cache"]},
                    "ignoreViolations": {"*": ["/tmp"]}, "enableWeakerNestedSandbox": True}})
    write(layout["home"] / ".tddagents" / "settings.json", {"permissions": {"allow": ["Edit(/notes)"]}})
    a = adapter(layout, locs=locations(layout, extra=("/session/dir",)))
    config = a.convert_to_sandbox_runtime_config(a.settings())
    assert config.network.allowed_domains == ["pypi.org", "docs.python.org"]
    assert config.network.denied_domains == ["evil.io"]
    assert config.network.allow_local_binding is True and config.network.allow_unix_sockets == ["/var/run/x.sock"]
    assert config.network.http_proxy_port == 3000 and config.network.socks_proxy_port is None
    fs = config.filesystem
    assert fs.allow_write == [".", TMP, "/data/shared", "/session/dir", f"{layout['home']}/.tddagents/notes",
                              f"{project}/gen/**", "/opt/cache"]
    assert fs.deny_write[:5] == [
        f"{layout['home']}/.tddagents/settings.json", f"{project}/.tddagents/settings.json",
        f"{project}/.tddagents/settings.local.json", f"{layout['managed']}/managed-settings.json",
        f"{layout['managed']}/managed-settings.d"]
    assert fs.deny_write[5:] == [f"{project}/.tddagents/skills", f"{project}/HEAD", f"{project}/secret",
                                 f"{project}/.tddagents/project.json"]
    # `~` in sandbox.filesystem.* is expanded here (expandPath), against the real home.
    assert fs.deny_read == ["/etc/shadow", os.path.expanduser("~")]
    assert fs.allow_read == [str(project)]
    assert config.ignore_violations == {"*": ["/tmp"]} and config.enable_weaker_nested_sandbox is True
    assert config.ripgrep is not None and config.ripgrep.command == "/opt/rg"


def test_a_moved_cwd_denies_its_settings_and_skills_too(layout: dict[str, Path]) -> None:
    moved = layout["project"] / "sub"
    moved.mkdir()
    a = adapter(layout, cwd=str(moved))
    deny = a.convert_to_sandbox_runtime_config({}).filesystem.deny_write
    assert f"{moved}/.tddagents/settings.json" in deny and f"{moved}/.tddagents/settings.local.json" in deny
    assert f"{moved}/.tddagents/skills" in deny
    assert f"{moved}/HEAD" not in deny  # missing: scrubbed after each command instead


def test_managed_domains_only_uses_the_policys_domains(layout: dict[str, Path]) -> None:
    write(layout["managed"] / "managed-settings.json", {
        "permissions": {"allow": ["WebFetch(domain:corp.io)"]},
        "sandbox": {"network": {"allowManagedDomainsOnly": True, "allowedDomains": ["mirror.corp.io"]},
                    "filesystem": {"allowManagedReadPathsOnly": True, "allowRead": ["/opt/policy"]}}})
    project_settings(layout, {"permissions": {"allow": ["WebFetch(domain:pypi.org)"]},
                              "sandbox": {"network": {"allowedDomains": ["npmjs.org"]},
                                          "filesystem": {"allowRead": ["/mine"]}}})
    a = adapter(layout)
    config = a.convert_to_sandbox_runtime_config(a.settings())
    assert config.network.allowed_domains == ["mirror.corp.io", "corp.io"]
    assert config.filesystem.allow_read == ["/opt/policy"]


def test_a_ripgrep_setting_wins(layout: dict[str, Path]) -> None:
    a = adapter(layout)
    config = a.convert_to_sandbox_runtime_config({"sandbox": {"ripgrep": {"command": "/x/rg", "args": ["-j1"]}}})
    assert config.ripgrep is not None and (config.ripgrep.command, config.ripgrep.args) == ("/x/rg", ["-j1"])


def test_the_worktree_main_repository(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /repo/main/.git/worktrees/wt\n")
    assert detect_worktree_main_repo_path(str(worktree)) == "/repo/main"
    (worktree / ".git").write_text("gitdir: ../elsewhere\n")
    assert detect_worktree_main_repo_path(str(worktree)) is None
    (worktree / ".git").write_text("nothing")
    assert detect_worktree_main_repo_path(str(worktree)) is None
    assert detect_worktree_main_repo_path(str(tmp_path / "none")) is None


def test_a_worktree_makes_its_main_repository_writable(layout: dict[str, Path]) -> None:
    (layout["project"] / ".git").write_text("gitdir: /repo/main/.git/worktrees/p\n")
    project_settings(layout, {"sandbox": {"enabled": True}})
    a = adapter(layout)
    a.initialize()
    assert a.base.get_config() is not None
    assert "/repo/main" in a.base.get_config().filesystem.allow_write  # type: ignore[union-attr]


# ── enablement ──────────────────────────────────────────────────────────────────


def test_enablement_follows_the_settings_the_platform_and_the_dependencies(layout: dict[str, Path]) -> None:
    assert adapter(layout).is_sandboxing_enabled() is False
    assert adapter(layout).get_sandbox_unavailable_reason() is None
    project_settings(layout, {"sandbox": {"enabled": True}})
    assert adapter(layout).is_sandboxing_enabled() is True
    assert adapter(layout, platform="wsl").is_sandboxing_enabled() is True
    assert adapter(layout, missing=("socat",)).is_sandboxing_enabled() is False
    assert adapter(layout, probe="bubblewrap (bwrap) cannot create a sandbox: x").is_sandboxing_enabled() is False


@pytest.mark.parametrize("platform, wsl, missing, probe, listed, reason", [
    ("macos", None, (), None, None,
     "sandbox.enabled is set but macos is not supported (requires macOS, Linux, or WSL2)"),
    ("wsl", "1", (), None, None, "sandbox.enabled is set but WSL1 is not supported (requires WSL2)"),
    ("linux", None, (), None, ["macos"], "sandbox.enabled is set but linux is not in sandbox.enabledPlatforms"),
    ("linux", None, ("socat", "bwrap"), None, None,
     "sandbox.enabled is set but dependencies are missing: bubblewrap (bwrap) not installed, socat not installed"
     " · install missing tools (e.g. apt install bubblewrap socat) or run /sandbox for details"),
    ("linux", None, (), "bubblewrap (bwrap) cannot create a sandbox: No permissions", None,
     "sandbox.enabled is set but dependencies are missing: bubblewrap (bwrap) cannot create a sandbox: No "
     "permissions · install missing tools (e.g. apt install bubblewrap socat) or run /sandbox for details"),
])
def test_unavailable_reasons(layout: dict[str, Path], platform: str, wsl: str | None, missing: tuple[str, ...],
                             probe: str | None, listed: list[str] | None, reason: str) -> None:
    sandbox: dict[str, Any] = {"enabled": True}
    if listed is not None:
        sandbox["enabledPlatforms"] = listed
    project_settings(layout, {"sandbox": sandbox})
    a = adapter(layout, platform=platform, wsl_version=wsl, missing=missing, probe=probe)
    assert a.get_sandbox_unavailable_reason() == reason
    assert a.is_sandboxing_enabled() is False


def test_the_probe_runs_once_and_only_without_another_error(layout: dict[str, Path]) -> None:
    calls: list[int] = []

    def probe() -> str | None:
        calls.append(1)
        return None

    project_settings(layout, {"sandbox": {"enabled": True}})
    a = SandboxAdapter(locations(layout), platform="linux", which=lambda n: f"/usr/bin/{n}", probe=probe,
                       ripgrep_command="/opt/rg", temp_dir=TMP)
    a.is_sandboxing_enabled()
    a.is_sandboxing_enabled()
    assert calls == [1]
    b = SandboxAdapter(locations(layout), platform="linux", which=lambda n: None, probe=probe,
                       ripgrep_command="/opt/rg", temp_dir=TMP)
    assert "socat not installed" in b.check_dependencies().errors
    assert calls == [1]


def test_an_empty_enabled_platforms_list_disables_everywhere(layout: dict[str, Path]) -> None:
    project_settings(layout, {"sandbox": {"enabled": True, "enabledPlatforms": []}})
    assert adapter(layout).is_platform_in_enabled_list() is False


def test_the_switches(layout: dict[str, Path]) -> None:
    a = adapter(layout)
    assert a.is_auto_allow_bash_if_sandboxed_enabled() and a.are_unsandboxed_commands_allowed()
    assert not a.is_sandbox_required() and a.get_excluded_commands() == []
    project_settings(layout, {"sandbox": {"enabled": True, "failIfUnavailable": True,
                                          "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False,
                                          "excludedCommands": ["docker"]}})
    assert a.is_sandbox_required() and not a.is_auto_allow_bash_if_sandboxed_enabled()
    assert not a.are_unsandboxed_commands_allowed() and a.get_excluded_commands() == ["docker"]
    project_settings(layout, {"sandbox": {"failIfUnavailable": True}})
    assert not a.is_sandbox_required()


def test_settings_locked_by_flag_or_policy(layout: dict[str, Path]) -> None:
    assert adapter(layout).are_sandbox_settings_locked_by_policy() is False
    write(layout["flag"], {"sandbox": {"enabled": False}})
    assert adapter(layout, locs=locations(layout, flag=True)).are_sandbox_settings_locked_by_policy() is True
    write(layout["managed"] / "managed-settings.json", {"sandbox": {"allowUnsandboxedCommands": False}})
    assert adapter(layout).are_sandbox_settings_locked_by_policy() is True


def test_set_sandbox_settings_and_excluded_commands_write_the_local_settings(layout: dict[str, Path]) -> None:
    a = adapter(layout)
    local = layout["project"] / ".tddagents" / "settings.local.json"
    a.set_sandbox_settings(enabled=True)
    a.set_sandbox_settings(auto_allow_bash_if_sandboxed=False, allow_unsandboxed_commands=False)
    assert json.loads(local.read_text())["sandbox"] == {
        "enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False}
    locs = locations(layout)
    assert add_to_excluded_commands(locs, "docker ps") == "docker ps"
    rules = [{"toolName": "Read"}, {"toolName": "Bash", "ruleContent": "npm run test:*"}]
    updates = [{"type": "addRules", "rules": rules}]
    assert add_to_excluded_commands(locs, "npm run test -- x", updates) == "npm run test"
    assert add_to_excluded_commands(locs, "docker ps") == "docker ps"
    assert add_to_excluded_commands(locs, "make", [{"type": "addRules", "rules": [{"toolName": "Bash"}]}]) == "make"
    sandbox = json.loads(local.read_text())["sandbox"]
    assert sandbox["excludedCommands"] == ["docker ps", "npm run test", "make"] and sandbox["enabled"] is True


def test_glob_warnings_for_edit_and_read_rules(layout: dict[str, Path]) -> None:
    project_settings(layout, {"permissions": {"allow": ["Edit(src/*.py)", "Edit(build/**)", "Bash(ls *)"],
                                              "deny": ["Read(**/.env)"]}, "sandbox": {"enabled": True}})
    assert adapter(layout).get_linux_glob_pattern_warnings() == ["Edit(src/*.py)", "Read(**/.env)"]
    assert adapter(layout, platform="macos").get_linux_glob_pattern_warnings() == []
    project_settings(layout, {"permissions": {"allow": ["Edit(src/*.py)"]}})
    assert adapter(layout).get_linux_glob_pattern_warnings() == []


# ── lifecycle ───────────────────────────────────────────────────────────────────


class Detector:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


def test_initialize_refresh_and_reset(layout: dict[str, Path]) -> None:
    network, detector = Network(), Detector()
    a = adapter(layout, network=network)
    a.initialize(change_detector=detector)
    assert network.calls == 0 and detector.listeners == [], "nothing starts while the settings disable it"
    project_settings(layout, {"sandbox": {"enabled": True, "network": {"allowedDomains": ["pypi.org"]}}})
    a.initialize(change_detector=detector)
    a.initialize(change_detector=detector)
    assert network.calls == 1 and len(detector.listeners) == 1
    project_settings(layout, {"sandbox": {"enabled": True, "network": {"allowedDomains": ["npmjs.org"]}}})
    detector.listeners[0]("projectSettings")
    config = a.base.get_config()
    assert config is not None and config.network.allowed_domains == ["npmjs.org"]
    a.reset()
    assert network.closed == 1 and detector.listeners == []
    a.refresh_config()  # still enabled in settings: the base is updated even after a reset
    project_settings(layout, {})
    a.refresh_config()


def test_wrapping_an_enabled_sandbox_needs_initialize(layout: dict[str, Path]) -> None:
    project_settings(layout, {"sandbox": {"enabled": True}})
    a = adapter(layout)
    with pytest.raises(RuntimeError, match="Sandbox failed to initialize"):
        a.wrap_with_sandbox("ls")
    a.initialize()
    assert a.wrap_with_sandbox("ls", "/bin/bash").startswith("bwrap --new-session --die-with-parent --unshare-net")
    assert a.wait_for_network_initialization() and a.get_proxy_port() == 1 and a.get_socks_proxy_port() == 2


def test_an_initialize_failure_is_a_sandbox_unavailable_error(layout: dict[str, Path]) -> None:
    project_settings(layout, {"sandbox": {"enabled": True}})

    def boom(_filter: Any, **_kw: Any) -> HostNetworkContext:
        raise RuntimeError("Failed to start HTTP bridge process")

    a = adapter(layout, network=boom)
    with pytest.raises(SandboxUnavailableError, match="Failed to start HTTP bridge process"):
        a.initialize()


def test_the_ask_is_refused_under_managed_domains_only(layout: dict[str, Path]) -> None:
    project_settings(layout, {"sandbox": {"enabled": True}})
    network = Network()
    asked: list[str] = []

    async def ask(pattern: Any) -> bool:
        asked.append(pattern["host"])
        return True

    a = adapter(layout, network=network)
    a.initialize(ask)
    assert asyncio.run(network.filter(443, "unknown.io")) is True and asked == ["unknown.io"]
    write(layout["managed"] / "managed-settings.json", {"sandbox": {"network": {"allowManagedDomainsOnly": True}}})
    assert asyncio.run(network.filter(443, "other.io")) is False and asked == ["unknown.io"]


def test_cleanup_scrubs_planted_bare_repo_files(layout: dict[str, Path]) -> None:
    project = layout["project"]
    (project / "config").write_text("kept: it existed before")
    a = adapter(layout)
    a.convert_to_sandbox_runtime_config({})
    (project / "HEAD").write_text("ref: refs/heads/main")
    (project / "objects").mkdir()
    (project / "objects" / "x").write_text("")
    a.cleanup_after_command()
    assert not (project / "HEAD").exists() and not (project / "objects").exists()
    assert (project / "config").exists()


def test_the_forwarders(layout: dict[str, Path]) -> None:
    network = {"allowLocalBinding": True, "allowUnixSockets": ["/s"]}
    project_settings(layout, {"sandbox": {"enabled": True, "network": network, "ignoreViolations": {"x": []},
                                          "enableWeakerNestedSandbox": False}})
    a = adapter(layout)
    a.initialize()
    assert a.get_fs_write_config().allow_only[6] == TMP and a.get_fs_read_config().deny_only == []
    assert a.get_network_restriction_config().allowed_hosts is None
    assert a.get_allow_local_binding() is True and a.get_allow_unix_sockets() == ["/s"]
    assert a.get_ignore_violations() == {"x": []} and a.get_enable_weaker_nested_sandbox() is False
    assert a.get_linux_http_socket_path() is None and a.get_linux_socks_socket_path() is None
    assert a.get_sandbox_violation_store().get_count() == 0
    assert a.annotate_stderr_with_sandbox_failures("ls", "e") == "e"


def test_the_default_ripgrep_is_the_vendored_one(layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils.ripgrep import builtin_ripgrep_path

    a = SandboxAdapter(locations(layout), platform="linux")
    assert a.ripgrep_config().command == builtin_ripgrep_path()
    monkeypatch.setenv("USE_BUILTIN_RIPGREP", "0")
    assert a.ripgrep_config().command == "rg"


# ── /doctor and the UI helper ───────────────────────────────────────────────────


def test_the_doctor_section(layout: dict[str, Path]) -> None:
    assert sandbox_doctor_section(None) == []
    assert sandbox_doctor_section(adapter(layout)) == []
    project_settings(layout, {"sandbox": {"enabled": True}})
    assert sandbox_doctor_section(adapter(layout, platform="macos")) == []
    assert sandbox_doctor_section(adapter(layout)) == []
    assert sandbox_doctor_section(adapter(layout, missing=("socat",))) == [
        "Sandbox", "└ Status: Missing dependencies", "└ socat not installed", "└ Run /sandbox for install instructions"]

    class WarnOnly:
        def is_supported_platform(self) -> bool:
            return True

        def is_sandbox_enabled_in_settings(self) -> bool:
            return True

        def check_dependencies(self) -> Any:
            from app.utils.sandbox.runtime.linux_sandbox_utils import SandboxDependencyCheck

            return SandboxDependencyCheck(errors=[], warnings=["seccomp not available"])

    assert sandbox_doctor_section(WarnOnly()) == ["Sandbox", "└ Status: Available (with warnings)",
                                                  "└ seccomp not available"]


def test_doctor_lines_include_the_sandbox_section(layout: dict[str, Path]) -> None:
    from app.screens.doctor import DoctorContext, doctor_lines

    project_settings(layout, {"sandbox": {"enabled": True}})
    lines = asyncio.run(doctor_lines(DoctorContext(locations=locations(layout),
                                                   sandbox=adapter(layout, missing=("bwrap",)))))
    assert "└ bubblewrap (bwrap) not installed" in lines
    assert lines.index("Sandbox") < len(lines)


def test_remove_sandbox_violation_tags() -> None:
    assert remove_sandbox_violation_tags("a<sandbox_violations>\nx\n</sandbox_violations>b") == "ab"
