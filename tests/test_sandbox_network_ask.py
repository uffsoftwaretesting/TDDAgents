"""
The network ask: `SandboxPermissionRequest`'s dialog in the REPL (Yes / Yes, and don't ask again / No; one
dialog per host; "don't ask again" persists `WebFetch(domain:…)` and refreshes the sandbox), the driver's
callback, and the whole path from a proxy request on its own thread to the REPL's loop and back.
"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.components.permissions.sandbox_permission_request import (
    DENIED,
    NO,
    QUESTION,
    TITLE,
    YES,
    YES_DONT_ASK_AGAIN,
    SandboxPermissionResponse,
    parse_sandbox_permission_answer,
    render_sandbox_permission_request,
    sandbox_permission_options,
)
from app.screens.repl import Repl
from app.state.app_state_store import AppStateStore
from app.tool import tool_context_for
from app.types.permissions import PermissionRuleSource, ToolPermissionContext
from app.utils.settings.settings import SettingsLocations


class FakeIO:
    def __init__(self) -> None:
        self.out: list[str] = []

    def write(self, text: str) -> None:
        self.out.append(text)


# ── the dialog ─────────────────────────────────────────────────────────────────


def test_the_options_hide_dont_ask_again_under_managed_domains_only() -> None:
    assert sandbox_permission_options("pypi.org", False) == [
        (YES, "Yes"), (YES_DONT_ASK_AGAIN, "Yes, and don't ask again for pypi.org"),
        (NO, "No, and tell TDDAgents what to do differently (esc)")]
    assert [value for value, _ in sandbox_permission_options("pypi.org", True)] == [YES, NO]


def test_the_dialog_text_and_the_answers() -> None:
    options = sandbox_permission_options("x.io", False)
    assert render_sandbox_permission_request("x.io", options) == "\n".join([
        f"⚠ {TITLE}", "Host: x.io", QUESTION, "  1. Yes", "  2. Yes, and don't ask again for x.io",
        "  3. No, and tell TDDAgents what to do differently (esc)", "Choose 1-3."])
    assert parse_sandbox_permission_answer(options, "1") == SandboxPermissionResponse(True, False)
    assert parse_sandbox_permission_answer(options, " 2 ") == SandboxPermissionResponse(True, True)
    assert parse_sandbox_permission_answer(options, "3") == DENIED
    for line in ("", "0", "4", "yes"):
        assert parse_sandbox_permission_answer(options, line) is None


# ── the REPL ───────────────────────────────────────────────────────────────────


class Sandbox:
    def __init__(self) -> None:
        self.refreshed = 0

    def refresh_config(self) -> None:
        self.refreshed += 1


def repl_for(tmp_path: Path, *, managed_only: bool = False) -> tuple[Repl, Any, FakeIO]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    managed = tmp_path / "etc"
    if managed_only:
        managed.mkdir()
        (managed / "managed-settings.json").write_text(json.dumps(
            {"sandbox": {"network": {"allowManagedDomainsOnly": True}}}))
    locations = SettingsLocations(project_root=project, home=tmp_path / "home", managed_dir=managed)
    store = AppStateStore()
    store.update(lambda state: replace(state, tool_permission_context=ToolPermissionContext(
        settings_locations=locations)))
    ctx = tool_context_for(store)
    run = SimpleNamespace(on_message=None, ctx=ctx, store=store, settings_locations=locations, sandbox=Sandbox())
    io = FakeIO()
    return Repl(run, io), run, io  # type: ignore[arg-type]


async def answer(repl: Repl, line: str) -> None:
    for _ in range(200):
        if repl.pending is not None:
            break
        await asyncio.sleep(0.005)
    assert repl.pending is not None
    parsed = repl.pending.parse(line)
    repl.pending.future.set_result(parsed)
    await asyncio.sleep(0.01)


def test_yes_allows_once_and_no_denies(tmp_path: Path) -> None:
    async def go() -> list[bool]:
        repl, run, io = repl_for(tmp_path)
        first = asyncio.ensure_future(repl.ask_sandbox_permission({"host": "a.io", "port": 443}))
        await answer(repl, "1")
        second = asyncio.ensure_future(repl.ask_sandbox_permission({"host": "a.io", "port": 443}))
        await answer(repl, "3")
        assert run.sandbox.refreshed == 0 and sum(TITLE in text for text in io.out) == 2
        return [await first, await second]

    assert asyncio.run(go()) == [True, False]


def test_requests_for_the_same_host_share_one_dialog(tmp_path: Path) -> None:
    async def go() -> list[bool]:
        repl, _run, io = repl_for(tmp_path)
        tasks = [asyncio.ensure_future(repl.ask_sandbox_permission({"host": "b.io", "port": 443})) for _ in range(3)]
        await answer(repl, "1")
        results = list(await asyncio.gather(*tasks))
        assert sum(TITLE in text for text in io.out) == 1
        return results

    assert asyncio.run(go()) == [True, True, True]


def test_dont_ask_again_persists_the_domain_and_refreshes_the_sandbox(tmp_path: Path) -> None:
    async def go() -> tuple[bool, Any, Any]:
        repl, run, _io = repl_for(tmp_path)
        task = asyncio.ensure_future(repl.ask_sandbox_permission({"host": "c.io", "port": 443}))
        await answer(repl, "2")
        return await task, run, None

    allowed, run, _ = asyncio.run(go())
    assert allowed is True and run.sandbox.refreshed == 1
    local = tmp_path / "project" / ".tddagents" / "settings.local.json"
    assert json.loads(local.read_text())["permissions"]["allow"] == ["WebFetch(domain:c.io)"]
    rules = run.store.get().tool_permission_context.always_allow_rules
    assert [(r.tool_name, r.rule_content, r.source) for r in rules] == [
        ("WebFetch", "domain:c.io", PermissionRuleSource.LOCAL_SETTINGS)]


def test_esc_denies_without_aborting_the_turn(tmp_path: Path) -> None:
    async def go() -> bool:
        repl, _run, _io = repl_for(tmp_path)
        task = asyncio.ensure_future(repl.ask_sandbox_permission({"host": "d.io", "port": 80}))
        for _ in range(200):
            if repl.pending is not None:
                break
            await asyncio.sleep(0.005)
        assert repl.pending is not None and repl.pending.aborts_turn is False
        repl.interrupt()
        return await task

    assert asyncio.run(go()) is False


def test_managed_domains_only_offers_no_dont_ask_again(tmp_path: Path) -> None:
    async def go() -> list[str]:
        repl, _run, io = repl_for(tmp_path, managed_only=True)
        task = asyncio.ensure_future(repl.ask_sandbox_permission({"host": "e.io", "port": 443}))
        await answer(repl, "2")
        assert await task is False
        return io.out

    out = asyncio.run(go())
    assert "don't ask again" not in "\n".join(out)


# ── the driver's callback and the whole path ───────────────────────────────────


def test_the_drivers_callback_routes_to_the_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.session.driver as driver

    captured: dict[str, Any] = {}

    class Stop(Exception):
        pass

    def fake_open_session_run(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise Stop

    async def no_trust(*_args: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr("app.cli.print.open_session_run", fake_open_session_run)
    monkeypatch.setattr("app.components.trust_dialog.trust_dialog.show_trust_dialog", no_trust)
    monkeypatch.setattr("app.interactive_helpers.show_mcpjson_server_approvals", no_trust)
    monkeypatch.setattr("app.interactive_helpers.launch_invalid_settings_dialog", no_trust)

    class IO:
        def __enter__(self) -> IO:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    from app.workspace.local import LocalWorkspace

    with pytest.raises(Stop):
        driver.develop_interactive("t-sbx", "x", LocalWorkspace("/tmp/t-sbx-project"),
                                   driver.SessionOptions(), io_factory=lambda _on_interrupt: IO())
    ask = captured["sandbox_ask_callback"]
    assert asyncio.run(ask({"host": "f.io", "port": 443})) is False, "no REPL yet: denied"


def test_a_proxy_request_is_asked_in_the_repl_and_answered(tmp_path: Path) -> None:
    from app.utils.sandbox.runtime.network_host import call_on_loop, start_host_network

    async def go() -> tuple[bytes, bytes]:
        repl, _run, _io = repl_for(tmp_path)
        ask = call_on_loop(repl.ask_sandbox_permission, asyncio.get_running_loop())

        async def filter_request(port: int, host: str) -> bool:
            return await ask({"host": host, "port": port})

        context = start_host_network(filter_request, linux=False)

        def connect(target: str) -> bytes:
            with socket.create_connection(("127.0.0.1", context.http_proxy_port), timeout=10) as conn:
                conn.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
                return conn.recv(1024)

        try:
            refused = asyncio.ensure_future(asyncio.to_thread(connect, "nowhere.invalid:443"))
            await answer(repl, "3")
            return await refused, b""
        finally:
            context.close()

    refused, _ = asyncio.run(go())
    assert refused.startswith(b"HTTP/1.1 403 Forbidden")
