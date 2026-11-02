"""`app/cli/mcp_auth_control.py`: print.ts's `mcp_authenticate`, `mcp_oauth_callback_url` and `mcp_clear_auth`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.cli import mcp_auth_control as ctl
from app.services.mcp import auth as ma
from app.services.mcp.mcp_connection_manager import ReconnectResult
from app.services.mcp.types import McpServerConfig, NeedsAuthMCPServer
from app.utils.abort_controller import AbortSignal
from app.utils.secure_storage.index import get_secure_storage
from tests.oauth_doubles import fake_http_server, install_authorization_server

HTTP = McpServerConfig(type="http", scope="user", url="https://m/mcp")


class Host:
    """What print.ts reads from its closure, recorded."""

    def __init__(self, configs: dict[str, McpServerConfig], *, disabled: bool = False) -> None:
        self.configs = configs
        self.disabled = disabled
        self.reconnected: list[str] = []
        self.applied: list[tuple[str, ReconnectResult]] = []

    def find_config(self, name: str) -> McpServerConfig | None:
        return self.configs.get(name)

    async def reconnect(self, name: str, config: McpServerConfig) -> ReconnectResult:
        self.reconnected.append(name)
        return ReconnectResult(client=NeedsAuthMCPServer(name=name, config=config))

    def apply(self, name: str, result: ReconnectResult) -> None:
        self.applied.append((name, result))

    def kwargs(self, flows: ctl.McpOAuthFlows, flow: Any) -> dict[str, Any]:
        return {
            "flows": flows, "find_config": self.find_config, "reconnect": self.reconnect,
            "apply_reconnect": self.apply, "is_server_disabled": lambda name: self.disabled,
            "perform_oauth_flow": flow,
        }


class ScriptedFlow:
    """A `performMCPOAuthFlow` stand-in: announces a URL (or not), then waits to be released."""

    def __init__(self, url: str | None = "https://as/authorize?x=1", error: Exception | None = None,
                 fail_before_url: Exception | None = None) -> None:
        self.url = url
        self.error = error
        self.fail_before_url = fail_before_url
        self.release = asyncio.Event()
        self.calls: list[dict[str, Any]] = []
        self.signals: list[AbortSignal] = []
        self.submitted: list[str] = []

    async def __call__(self, name: str, config: McpServerConfig, on_url: Callable[[str], None],
                       signal: AbortSignal, **options: Any) -> None:
        self.calls.append({"name": name, "config": config, **options})
        self.signals.append(signal)
        options["on_waiting_for_callback"](self.submitted.append)
        if self.fail_before_url is not None:
            raise self.fail_before_url
        if self.url is None:
            return
        on_url(self.url)
        await self.release.wait()
        if self.error is not None:
            raise self.error


async def settle(flows: ctl.McpOAuthFlows) -> None:
    while flows.background:
        await asyncio.gather(*list(flows.background))


# ── mcp_authenticate ──


def test_authenticate_refuses_unknown_and_non_oauth_servers():
    host = Host({"local": McpServerConfig(type="stdio", scope="user", command="x"),
                 "untyped": McpServerConfig(type=None, scope="user", command="x"),
                 "ws": McpServerConfig(type="ws", scope="user", url="wss://x")})
    flows = ctl.McpOAuthFlows()

    def run(name: str) -> ctl.McpControlOutcome:
        return asyncio.run(ctl.handle_mcp_authenticate(name, **host.kwargs(flows, ScriptedFlow())))

    assert run("missing") == ctl.McpControlError("Server not found: missing")
    assert run("local") == ctl.McpControlError('Server type "stdio" does not support OAuth authentication')
    assert run("untyped") == ctl.McpControlError('Server type "undefined" does not support OAuth authentication')
    assert run("ws") == ctl.McpControlError('Server type "ws" does not support OAuth authentication')


def test_authenticate_answers_the_url_then_reconnects_in_the_background():
    host = Host({"srv": HTTP})

    async def scenario() -> tuple[ctl.McpControlOutcome, ctl.McpOAuthFlows, ScriptedFlow]:
        flows = ctl.McpOAuthFlows()
        flow = ScriptedFlow()
        outcome = await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, flow))
        assert host.reconnected == [] and "srv" in flows.oauth_auth_promises
        assert "srv" in flows.active_oauth_flows and "srv" in flows.oauth_callback_submitters
        flow.release.set()
        await settle(flows)
        return outcome, flows, flow

    outcome, flows, flow = asyncio.run(scenario())
    assert outcome == ctl.McpControlSuccess({"authUrl": "https://as/authorize?x=1", "requiresUserAction": True})
    assert flow.calls[0]["skip_browser_open"] is True and flow.calls[0]["config"] is HTTP
    assert host.reconnected == ["srv"] and host.applied[0][0] == "srv"
    assert not flows.active_oauth_flows and not flows.oauth_callback_submitters
    assert not flows.oauth_auth_promises and not flows.oauth_manual_callback_used


def test_a_flow_that_needs_no_browser_answers_without_user_action():
    host = Host({"srv": HTTP})

    async def scenario() -> ctl.McpControlOutcome:
        flows = ctl.McpOAuthFlows()
        outcome = await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, ScriptedFlow(url=None)))
        await settle(flows)
        return outcome

    assert asyncio.run(scenario()) == ctl.McpControlSuccess({"requiresUserAction": False})
    assert host.reconnected == ["srv"]


def test_a_flow_failing_before_its_url_is_the_error():
    host = Host({"srv": HTTP})

    async def scenario() -> tuple[ctl.McpControlOutcome, ctl.McpOAuthFlows]:
        flows = ctl.McpOAuthFlows()
        flow = ScriptedFlow(fail_before_url=RuntimeError("SDK auth failed: no registration"))
        return await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, flow)), flows

    outcome, flows = asyncio.run(scenario())
    assert outcome == ctl.McpControlError("SDK auth failed: no registration")
    assert host.reconnected == [] and "srv" not in flows.oauth_auth_promises


def test_a_failed_or_disabled_flow_does_not_reconnect(caplog):
    for host, flow in ((Host({"srv": HTTP}), ScriptedFlow(error=RuntimeError("denied"))),
                       (Host({"srv": HTTP}, disabled=True), ScriptedFlow())):

        async def scenario(host: Host = host, flow: ScriptedFlow = flow) -> ctl.McpOAuthFlows:
            flows = ctl.McpOAuthFlows()
            await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, flow))
            flow.release.set()
            await settle(flows)
            return flows

        with caplog.at_level("ERROR", logger=ctl.logger.name):
            flows = asyncio.run(scenario())
        assert host.reconnected == [] and not flows.active_oauth_flows
    assert "MCP OAuth failed for srv: denied" in caplog.text


def test_a_new_authenticate_aborts_the_previous_flow_and_keeps_its_own_entries():
    host = Host({"srv": HTTP})

    async def scenario() -> tuple[ScriptedFlow, ScriptedFlow, ctl.McpOAuthFlows]:
        flows = ctl.McpOAuthFlows()
        first, second = ScriptedFlow(), ScriptedFlow(url="https://as/second")
        await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, first))
        await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, second))
        first.release.set()
        while len(flows.background) > 1:
            await asyncio.sleep(0)
        assert "srv" in flows.active_oauth_flows and flows.active_oauth_flows["srv"].signal is second.signals[0]
        second.release.set()
        await settle(flows)
        return first, second, flows

    first, second, flows = asyncio.run(scenario())
    assert first.signals[0].aborted and not second.signals[0].aborted
    assert not flows.active_oauth_flows and host.reconnected == ["srv", "srv"]


def test_an_error_while_starting_is_reported():
    host = Host({"srv": HTTP})

    def flow(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("cannot start")

    outcome = asyncio.run(ctl.handle_mcp_authenticate("srv", **host.kwargs(ctl.McpOAuthFlows(), flow)))
    assert outcome == ctl.McpControlError("cannot start")


# ── mcp_oauth_callback_url ──


def test_a_callback_url_needs_an_active_flow_and_a_code_or_error():
    flows = ctl.McpOAuthFlows()
    assert asyncio.run(ctl.handle_mcp_oauth_callback_url("srv", "http://localhost/callback?code=c", flows=flows)) \
        == ctl.McpControlError("No active OAuth flow for server: srv")
    submitted: list[str] = []
    flows.oauth_callback_submitters["srv"] = submitted.append
    for bad in ("not a url", "http://localhost/callback?state=s", "http://localhost/callback"):
        outcome = asyncio.run(ctl.handle_mcp_oauth_callback_url("srv", bad, flows=flows))
        assert outcome == ctl.McpControlError(ctl.INVALID_CALLBACK_URL_ERROR)
    assert submitted == [] and not flows.oauth_manual_callback_used
    outcome = asyncio.run(ctl.handle_mcp_oauth_callback_url("srv", "http://localhost/callback?error=x", flows=flows))
    assert outcome == ctl.McpControlSuccess() and submitted == ["http://localhost/callback?error=x"]
    assert flows.oauth_manual_callback_used == {"srv"}


def test_a_pasted_callback_waits_for_the_exchange_and_skips_the_background_reconnect():
    host = Host({"srv": HTTP})

    async def scenario(error: Exception | None) -> tuple[ctl.McpControlOutcome, ScriptedFlow]:
        flows = ctl.McpOAuthFlows()
        flow = ScriptedFlow(error=error)
        await ctl.handle_mcp_authenticate("srv", **host.kwargs(flows, flow))
        pending = asyncio.ensure_future(
            ctl.handle_mcp_oauth_callback_url("srv", "http://localhost:1/callback?code=c&state=s", flows=flows))
        await asyncio.sleep(0.01)
        assert not pending.done()
        flow.release.set()
        outcome = await pending
        await settle(flows)
        return outcome, flow

    outcome, flow = asyncio.run(scenario(None))
    assert outcome == ctl.McpControlSuccess() and flow.submitted == ["http://localhost:1/callback?code=c&state=s"]
    assert host.reconnected == []
    outcome, _ = asyncio.run(scenario(RuntimeError("OAuth state mismatch - possible CSRF attack")))
    assert outcome == ctl.McpControlError("OAuth state mismatch - possible CSRF attack")
    outcome, _ = asyncio.run(scenario(RuntimeError()))
    assert outcome == ctl.McpControlError("")  # `error.message` of an Error without one


def test_the_real_flow_completes_from_a_pasted_callback_url():
    with fake_http_server() as srv:
        install_authorization_server(srv)
        config = McpServerConfig(type="http", scope="user", url=f"{srv.base}/mcp")
        host = Host({"srv": config})

        async def scenario() -> tuple[ctl.McpControlOutcome, ctl.McpControlOutcome]:
            flows = ctl.McpOAuthFlows()
            kwargs = host.kwargs(flows, ma.perform_mcp_oauth_flow)
            started = await ctl.handle_mcp_authenticate("srv", **kwargs)
            assert isinstance(started, ctl.McpControlSuccess) and started.response is not None
            params = {k: v[0] for k, v in parse_qs(urlsplit(started.response["authUrl"]).query).items()}
            callback = f"{params['redirect_uri']}?{urlencode({'code': 'pasted', 'state': params['state']})}"
            finished = await ctl.handle_mcp_oauth_callback_url("srv", callback, flows=flows)
            await settle(flows)
            return started, finished

        started, finished = asyncio.run(scenario())
        assert isinstance(started, ctl.McpControlSuccess) and started.response is not None
        assert started.response["requiresUserAction"] is True
        assert finished == ctl.McpControlSuccess() and host.reconnected == []
        assert srv.requests("POST", "/token")[0].form["code"] == "pasted"
        stored = (get_secure_storage().read() or {})["mcpOAuth"][ma.get_server_key("srv", config)]
        assert stored["accessToken"] == "tok-1"


# ── mcp_clear_auth ──


def test_clear_auth_revokes_then_reconnects():
    host = Host({"srv": HTTP, "local": McpServerConfig(type="stdio", scope="user", command="x")})
    revoked: list[tuple[str, McpServerConfig]] = []

    async def revoke(name: str, config: McpServerConfig) -> None:
        assert host.reconnected == []
        revoked.append((name, config))

    def run(name: str) -> ctl.McpControlOutcome:
        return asyncio.run(ctl.handle_mcp_clear_auth(
            name, find_config=host.find_config, reconnect=host.reconnect, apply_reconnect=host.apply, revoke=revoke))

    assert run("missing") == ctl.McpControlError("Server not found: missing")
    assert run("local") == ctl.McpControlError('Cannot clear auth for server type "stdio"')
    assert revoked == [] and host.reconnected == []
    assert run("srv") == ctl.McpControlSuccess({})
    assert revoked == [("srv", HTTP)] and host.reconnected == ["srv"] and host.applied[0][0] == "srv"


def test_clear_auth_defaults_to_revoking_the_stored_tokens():
    config = McpServerConfig(type="sse", scope="user", url="https://unreachable.invalid/sse")
    data = {"mcpOAuth": {ma.get_server_key("srv", config): {"serverName": "srv", "accessToken": ""}}}
    get_secure_storage().update(data)
    host = Host({"srv": config})
    outcome = asyncio.run(ctl.handle_mcp_clear_auth(
        "srv", find_config=host.find_config, reconnect=host.reconnect, apply_reconnect=host.apply))
    assert outcome == ctl.McpControlSuccess({})
    assert (get_secure_storage().read() or {})["mcpOAuth"] == {}
