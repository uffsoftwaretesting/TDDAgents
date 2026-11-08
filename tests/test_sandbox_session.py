"""
The session's sandbox (`app/cli/print.py::open_session_run`): upstream's startup check (a required sandbox
that cannot run refuses the session with upstream's text; one that is not required is announced as
disabled), TDDAgents' bwrap probe, and an enabled sandbox started for the run and torn down with it. Fake
`bwrap` and `socat` stand on `PATH`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.cli.print import SANDBOX_DISABLED_TEXT, SANDBOX_REQUIRED_TEXT, open_session_run
from app.tdd.plan import ItemStatus, TddPlan
from app.types.permissions import PermissionMode, ToolPermissionContext
from app.utils.sandbox.sandbox_adapter import SandboxUnavailableError
from app.workspace.local import LocalWorkspace
from tests.sandbox_doubles import bwrap_calls, install_fake_sandbox_tools

FINISHED = TddPlan.from_texts(["a"])
FINISHED = dataclasses.replace(FINISHED, items=(dataclasses.replace(FINISHED.items[0], status=ItemStatus.DONE),))


def _scripted() -> Any:
    from app.query.deps import get_production_deps

    async def model(state: Any, config: Any) -> AsyncIterator[Any]:
        raise AssertionError("no model call in this test")
        yield  # pragma: no cover

    return dataclasses.replace(get_production_deps(), call_model=model)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A project, a fake-tool `PATH` (only the fakes and the shell basics) and the bwrap log."""
    bin_dir = tmp_path / "bin"
    log = install_fake_sandbox_tools(bin_dir)
    for tool in ("sh", "bash", "tail", "env", "cat", "pwd"):
        real = next((Path(d) / tool for d in os.environ["PATH"].split(os.pathsep) if (Path(d) / tool).exists()), None)
        if real is not None and not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(real)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_BWRAP_LOG", str(log))
    root = tmp_path / "ws"
    root.mkdir()
    return {"root": root, "bin": bin_dir, "log": log, "tmp": tmp_path}


def settings(project: dict[str, Any], sandbox: dict[str, Any]) -> None:
    path = project["root"] / ".tddagents" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sandbox": sandbox}))


async def _open(project: dict[str, Any], **kw: Any) -> Any:
    workspace = LocalWorkspace(str(project["root"]))
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, should_avoid_permission_prompts=True)
    seen: dict[str, Any] = {"workspace": workspace}
    async with open_session_run(run_id="sbx", plan=FINISHED, workspace=workspace, deps=_scripted(),
                                permission_context=perm, home=project["tmp"] / "home",
                                managed_dir=project["tmp"] / "etc", **kw) as run:
        seen["run"] = run
        seen["ctx_sandbox"] = run.ctx.sandbox_manager
        seen["ws_sandbox"] = workspace.sandbox_manager
        seen["enabled"] = run.sandbox.is_sandboxing_enabled()
        seen["proxy"] = run.sandbox.get_proxy_port()
        seen["http_socket"] = run.sandbox.get_linux_http_socket_path()
    seen["ws_sandbox_after"] = workspace.sandbox_manager
    return seen


def test_a_required_sandbox_without_socat_refuses_the_session(project: dict[str, Any]) -> None:
    (project["bin"] / "socat").unlink()
    settings(project, {"enabled": True, "failIfUnavailable": True})
    with pytest.raises(SandboxUnavailableError) as refused:
        asyncio.run(_open(project))
    reason = ("sandbox.enabled is set but dependencies are missing: socat not installed · install missing tools "
              "(e.g. apt install bubblewrap socat) or run /sandbox for details")
    assert str(refused.value) == SANDBOX_REQUIRED_TEXT.format(reason=reason)
    assert str(refused.value) == (
        f"\nError: sandbox required but unavailable: {reason}\n"
        "  sandbox.failIfUnavailable is set — refusing to start without a working sandbox.\n\n")
    assert bwrap_calls(project["log"]) == [], "the probe runs only when nothing else is missing"


def test_a_required_sandbox_whose_bwrap_cannot_create_a_namespace_refuses(
        project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_BWRAP_PROBE_EXIT", "1")
    settings(project, {"enabled": True, "failIfUnavailable": True})
    with pytest.raises(SandboxUnavailableError, match="bubblewrap \\(bwrap\\) cannot create a sandbox: bwrap: No "
                                                      "permissions to create new namespace"):
        asyncio.run(_open(project))
    assert bwrap_calls(project["log"]) == [["--ro-bind", "/", "/", "--unshare-net", "true"]]


def test_an_unrequired_sandbox_that_cannot_run_is_announced_and_the_session_opens(
        project: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    (project["bin"] / "bwrap").unlink()
    settings(project, {"enabled": True})
    seen = asyncio.run(_open(project))
    assert seen["enabled"] is False and seen["proxy"] is None
    reason = ("sandbox.enabled is set but dependencies are missing: bubblewrap (bwrap) not installed · install "
              "missing tools (e.g. apt install bubblewrap socat) or run /sandbox for details")
    assert SANDBOX_DISABLED_TEXT.format(reason=reason) in capsys.readouterr().err


def test_an_enabled_sandbox_starts_with_the_run_and_stops_with_it(project: dict[str, Any]) -> None:
    settings(project, {"enabled": True, "failIfUnavailable": True, "network": {"allowedDomains": ["pypi.org"]}})
    seen = asyncio.run(_open(project))
    run = seen["run"]
    assert seen["enabled"] is True and seen["ctx_sandbox"] is run.sandbox and seen["ws_sandbox"] is run.sandbox
    assert seen["proxy"] and seen["http_socket"] and Path(seen["http_socket"]).name.startswith("tddagents-http-")
    # Torn down: the bridge socket removed, the workspace detached.
    assert not os.path.exists(seen["http_socket"]) and seen["ws_sandbox_after"] is None
    assert run.sandbox.get_proxy_port() is None


def test_a_disabled_sandbox_is_attached_but_not_started(project: dict[str, Any]) -> None:
    settings(project, {"enabled": False})
    seen = asyncio.run(_open(project))
    assert seen["enabled"] is False and seen["ctx_sandbox"] is seen["run"].sandbox and seen["proxy"] is None
    assert bwrap_calls(project["log"]) == []


def test_flag_settings_turn_a_seeded_sandbox_off(project: dict[str, Any]) -> None:
    """How the suite opens a session over a project seeded with the shipped settings."""
    settings(project, {"enabled": True, "failIfUnavailable": True})
    (project["bin"] / "socat").unlink()
    flag = project["tmp"] / "flag.json"
    flag.write_text(json.dumps({"sandbox": {"enabled": False}}))
    seen = asyncio.run(_open(project, settings_path=str(flag)))
    assert seen["enabled"] is False


def test_a_sandbox_that_fails_to_start_is_a_sandbox_error(project: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils.sandbox.runtime import linux_network_bridge

    def broken(*_args: Any, **_kw: Any) -> Any:
        raise linux_network_bridge.BridgeError("Failed to start HTTP bridge process")

    monkeypatch.setattr("app.utils.sandbox.runtime.network_host.initialize_linux_network_bridge", broken)
    settings(project, {"enabled": True})
    with pytest.raises(SandboxUnavailableError) as failed:
        asyncio.run(_open(project))
    assert str(failed.value) == "\n❌ Sandbox Error: Failed to start HTTP bridge process\n"
