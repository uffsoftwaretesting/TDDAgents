"""
`/mcp` in the REPL: the server list, the stdio and remote menus, the tools views, the
authentication flow, and the `reconnect` / `enable` / `disable` argument forms
(`app/components/mcp/`), over a real `SessionMcp` whose reconnects and OAuth are faked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.cli.session_mcp import SessionMcp
from app.components.mcp.capabilities_section import capabilities_line
from app.components.mcp.mcp_command import call, mcp_toggle
from app.components.mcp.mcp_list_panel import (
    DISMISSED,
    get_scope_heading,
    render_list_panel,
    selectable_servers,
    server_status,
)
from app.components.mcp.mcp_reconnect import mcp_reconnect
from app.components.mcp.mcp_remote_server_menu import (
    COPY_URL,
    PASTE_URL,
    RECONNECT_FAILED,
    STILL_NEEDS_AUTH,
    remote_menu_options,
    render_remote_server,
)
from app.components.mcp.mcp_settings import NO_SERVERS, prepare_servers, run_mcp_settings
from app.components.mcp.mcp_stdio_server_menu import capitalize, render_stdio_server, stdio_menu_options
from app.components.mcp.mcp_tool_detail_view import render_tool_detail
from app.components.mcp.mcp_tool_list_view import tool_annotations, tool_display_name
from app.components.mcp.types import McpPanelContext, ServerInfo
from app.components.mcp.utils.reconnect_helpers import handle_reconnect_error, handle_reconnect_result
from app.services.mcp.auth import AuthenticationCancelledError
from app.services.mcp.config import get_global_config_path, is_mcp_server_disabled
from app.services.mcp.mcp_connection_manager import ReconnectResult
from app.services.mcp.normalization import extract_mcp_tool_display_name
from app.services.mcp.types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    McpOAuthConfig,
    McpServerConfig,
    NeedsAuthMCPServer,
)
from app.services.mcp.utils import describe_mcp_config_file_path, filter_tools_by_server
from app.utils.settings.settings import SettingsLocations
from tests.dialog_fakes import ESC, FakeDialog

STDIO = McpServerConfig(type="stdio", scope="project", command="npx", args=("srv", "--flag"))
HTTP = McpServerConfig(type="http", scope="user", url="https://mcp.example/api")
SSE = McpServerConfig(type="sse", scope="local", url="https://sse.example")


class Cleanup:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1


def connected(name: str, config: McpServerConfig = STDIO, cleanup: Any = None) -> ConnectedMCPServer:
    return ConnectedMCPServer(name=name, config=config, session=None, capabilities=None, cleanup=cleanup)


def tool(name: str, *, read_only: bool = False, destructive: bool = False, title: str | None = None,
         description: Any = "Does things", schema: Any = None) -> Any:
    server, _, short = name.removeprefix("mcp__").partition("__")

    def describe(_args: Any) -> str:
        if isinstance(description, Exception):
            raise description
        return str(description)

    return SimpleNamespace(
        name=name, user_facing_name=lambda _a: f"{server} - {title or short} (MCP)", description=describe,
        is_read_only=lambda _a: read_only, is_destructive=lambda _a: destructive,
        input_schema=schema or {"type": "object"})


def session(*clients: Any, tools: list[Any] | None = None, cwd: str = "/proj") -> SessionMcp:
    return SessionMcp(manager=SimpleNamespace(clients=list(clients), tools=list(tools or [])), cwd=cwd)


class Reconnects:
    """`reconnect_mcp_server_impl`, answering each server with a scripted result."""

    def __init__(self, results: dict[str, Callable[[str, McpServerConfig], ReconnectResult]] | None = None) -> None:
        self.calls: list[str] = []
        self.results = results or {}

    async def __call__(self, name: str, config: McpServerConfig, **_: Any) -> ReconnectResult:
        self.calls.append(name)
        make = self.results.get(name)
        return make(name, config) if make else ReconnectResult(client=connected(name, config))


def context(mcp: Any, *, tokens: dict[str, Any] | None = None, flow: Any = None, revoked: list[Any] | None = None,
            cwd: str = "/proj") -> McpPanelContext:
    async def read_tokens(name: str, config: McpServerConfig) -> Any:
        return (tokens or {}).get(name)

    async def revoke(name: str, config: McpServerConfig, **kwargs: Any) -> None:
        if revoked is not None:
            revoked.append((name, kwargs))

    async def no_flow(*args: Any, **kwargs: Any) -> None:
        return None

    return McpPanelContext(mcp=mcp, cwd=cwd, managed_dir=Path("/etc/tdd"), perform_oauth_flow=flow or no_flow,
                           revoke_server_tokens=revoke, read_tokens=read_tokens)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def info(client: Any, transport: str = "stdio", authenticated: bool | None = None) -> ServerInfo:
    return ServerInfo(client.name, client, client.config.scope, transport, client.config, authenticated)


# ── helpers ──────────────────────────────────────────────────────────────────

def test_helpers() -> None:
    assert capabilities_line(2, 0, 0) == "Capabilities: tools"
    assert capabilities_line(1, 1, 1) == "Capabilities: tools · resources · prompts"
    assert capabilities_line(0, 0, 0) == "Capabilities: none"
    assert capitalize("calc server") == "Calc server" and capitalize("") == ""
    tools = [SimpleNamespace(name="mcp__a_b__x"), SimpleNamespace(name="mcp__a__y"), SimpleNamespace(name=None)]
    assert filter_tools_by_server(tools, "a.b") == [tools[0]]
    assert extract_mcp_tool_display_name("calc - Add numbers (MCP)") == "Add numbers"
    assert extract_mcp_tool_display_name("  plain (MCP) ") == "plain"
    assert extract_mcp_tool_display_name("a - b - c") == "b - c"


def test_describe_mcp_config_file_path() -> None:
    global_path = str(get_global_config_path())
    assert describe_mcp_config_file_path("user", "/p") == global_path
    assert describe_mcp_config_file_path("project", "/p") == "/p/.mcp.json"
    assert describe_mcp_config_file_path("local", "/p") == f"{global_path} [project: /p]"
    assert describe_mcp_config_file_path("dynamic", "/p") == "Dynamically configured"
    assert describe_mcp_config_file_path("enterprise", "/p", "/etc/x") == "/etc/x/managed-mcp.json"
    assert describe_mcp_config_file_path("enterprise", "/p") == "managed-mcp.json"
    assert describe_mcp_config_file_path("claudeai", "/p") == "claude.ai"
    assert describe_mcp_config_file_path("plugin", "/p") == "plugin"


def test_reconnect_helpers() -> None:
    assert handle_reconnect_result(ReconnectResult(client=connected("a")), "a").message == "Reconnected to a."
    needs = handle_reconnect_result(ReconnectResult(client=NeedsAuthMCPServer("a", HTTP)), "a")
    assert needs.message == "a requires authentication. Use the 'Authenticate' option." and not needs.success
    assert handle_reconnect_result(ReconnectResult(client=FailedMCPServer("a", STDIO)), "a").message == (
        "Failed to reconnect to a.")
    assert handle_reconnect_result(ReconnectResult(client=DisabledMCPServer("a", STDIO)), "a").message == (
        "Unknown result when reconnecting to a.")
    assert handle_reconnect_error(RuntimeError("boom"), "a") == "Error reconnecting to a: boom"


# ── the list ─────────────────────────────────────────────────────────────────

def test_status_texts() -> None:
    assert server_status(connected("a")) == "✔ connected"
    assert server_status(DisabledMCPServer("a", STDIO)) == "◯ disabled"
    assert server_status(NeedsAuthMCPServer("a", HTTP)) == "△ needs authentication"
    assert server_status(FailedMCPServer("a", STDIO)) == "✘ failed"
    assert server_status(SimpleNamespace(type="pending")) == "◯ connecting…"
    assert server_status(SimpleNamespace(type="pending", reconnect_attempt=2, max_reconnect_attempts=5)) == (
        "◯ reconnecting (2/5)…")


def test_scope_headings() -> None:
    assert get_scope_heading("project", "/p") == ("Project MCPs", "/p/.mcp.json")
    assert get_scope_heading("user", "/p")[0] == "User MCPs"
    assert get_scope_heading("local", "/p")[0] == "Local MCPs"
    assert get_scope_heading("enterprise", "/p") == ("Enterprise MCPs", None)
    assert get_scope_heading("dynamic", "/p") == ("Built-in MCPs", "always available")
    assert get_scope_heading("other", "/p") == ("other", None)


def test_the_list_groups_by_scope_with_the_built_in_servers_last() -> None:
    dyn = McpServerConfig(type="sdk", scope="dynamic")
    servers = [info(connected("zeta", dyn)), info(FailedMCPServer("b", STDIO)), info(connected("a", STDIO)),
               info(NeedsAuthMCPServer("web", HTTP), "http")]
    assert [s.name for s in selectable_servers(servers)] == ["a", "b", "web", "zeta"]
    lines = render_list_panel(servers, "/p")
    assert lines[:4] == ["Manage MCP servers", "4 servers", "", "  Project MCPs (/p/.mcp.json)"]
    assert lines[4:6] == ["  1. a · ✔ connected", "  2. b · ✘ failed"]
    assert lines[-2:] == ["  Built-in MCPs (always available)", "  4. zeta · ✔ connected"]
    assert "  3. web · △ needs authentication" in lines
    assert render_list_panel(servers[:1], "/p")[1] == "1 server"


def test_prepare_servers() -> None:
    mcp = session(connected("web", HTTP), NeedsAuthMCPServer("sse", SSE), connected("ide", STDIO),
                  connected("calc", STDIO), connected("bare", HTTP), tools=[tool("mcp__web__x")])
    servers = run(prepare_servers(context(mcp, tokens={"sse": {"accessToken": "t"}})))
    assert [(s.name, s.transport, s.is_authenticated) for s in servers] == [
        ("bare", "http", False), ("calc", "stdio", None), ("sse", "sse", True), ("web", "http", True)]
    assert servers[1].scope == "project"


def test_no_servers_and_dismissing() -> None:
    assert run(run_mcp_settings(FakeDialog(), context(session()))) == NO_SERVERS
    assert run(run_mcp_settings(FakeDialog(ESC), context(session(connected("a"))))) == DISMISSED


# ── the stdio menu ───────────────────────────────────────────────────────────

def test_stdio_menu_rendering_and_options() -> None:
    ctx = context(session())
    server = info(connected("calc"))
    assert render_stdio_server(ctx, server, 2) == [
        "Calc MCP Server", "", "Status: ✔ connected", "Command: npx", "Args: srv --flag",
        "Config location: Dynamically configured", "Capabilities: tools", "Tools: 2 tools"]
    failed = info(FailedMCPServer("calc", McpServerConfig(type=None, scope="user", command="x")))
    assert render_stdio_server(ctx, failed, 0)[2:5] == [
        "Status: ✘ failed", "Command: x", "Config location: Dynamically configured"]
    assert render_stdio_server(ctx, info(connected("c")), 0)[-1] == "Capabilities: none"
    assert stdio_menu_options(server, 2) == [("View tools", "tools"), ("Reconnect", "reconnectMcpServer"),
                                             ("Disable", "toggle-enabled")]
    assert stdio_menu_options(server, 0)[0] == ("Reconnect", "reconnectMcpServer")
    assert stdio_menu_options(info(DisabledMCPServer("calc", STDIO)), 3) == [("Enable", "toggle-enabled")]
    pending = render_stdio_server(ctx, info(SimpleNamespace(type="pending", name="p", config=STDIO)), 0)
    assert pending[2] == "Status: ◯ connecting…"
    assert render_stdio_server(ctx, info(DisabledMCPServer("d", STDIO)), 0)[2] == "Status: ◯ disabled"


def test_the_config_location_comes_from_the_configured_servers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"calc": {"command": "npx"}}}')
    mcp = session(connected("calc"), cwd=str(tmp_path))
    mcp.settings_locations = lambda: SettingsLocations(project_root=tmp_path, home=tmp_path / "home",
                                                       managed_dir=tmp_path / "etc")
    lines = render_stdio_server(context(mcp, cwd=str(tmp_path)), info(connected("calc")), 0)
    assert f"Config location: {tmp_path / '.mcp.json'}" in lines


def test_reconnecting_a_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    reconnects = Reconnects({"calc": lambda n, c: ReconnectResult(client=FailedMCPServer(n, c))})
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", reconnects)
    cleanup = Cleanup()
    mcp = session(connected("calc", cleanup=cleanup), tools=[tool("mcp__calc__add")])
    dialog = FakeDialog("1", "2")
    assert run(run_mcp_settings(dialog, context(mcp))) == "Failed to reconnect to calc."
    assert reconnects.calls == ["calc"] and cleanup.calls == 1  # the old connection closed first
    assert "Reconnecting to calc\n Restarting MCP server process\nThis may take a few moments." in dialog.out
    assert mcp.manager.clients[0].type == "failed" and mcp.manager.tools == []


def test_a_failed_reconnect_is_the_dialogs_result() -> None:
    class Broken:
        cwd = "/proj"

        def all_clients(self) -> list[Any]:
            return [connected("calc")]

        def all_tools(self) -> list[Any]:
            return []

        async def reconnect(self, name: str, config: Any) -> Any:
            raise RuntimeError("no way")

        async def disable(self, name: str, config: Any) -> None:
            raise RuntimeError("locked")

    assert run(run_mcp_settings(FakeDialog("1", "1"), context(Broken()))) == "Error reconnecting to calc: no way"
    assert run(run_mcp_settings(FakeDialog("1", "2"), context(Broken()))) == (
        "Failed to disable MCP server 'calc': locked")


def test_disabling_returns_to_the_list_and_enabling_reconnects(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    reconnects = Reconnects()
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", reconnects)
    mcp = session(connected("calc"), cwd=str(tmp_path))
    dialog = FakeDialog("1", "2", "1", "1", ESC)
    assert run(run_mcp_settings(dialog, context(mcp, cwd=str(tmp_path)))) == DISMISSED
    assert not is_mcp_server_disabled("calc", tmp_path) and reconnects.calls == ["calc"]
    assert any("calc · ◯ disabled" in text for text in dialog.asked)
    assert dialog.asked[-1].startswith("Manage MCP servers") and "calc · ✔ connected" in dialog.asked[-1]


def test_the_tools_views(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {"type": "object", "properties": {"a": {"type": "number", "description": "first"}, "b": {}},
              "required": ["a"]}
    tools = [tool("mcp__calc__add", read_only=True, title="Add", schema=schema),
             tool("mcp__calc__wipe", destructive=True, description=RuntimeError("x")), tool("mcp__other__z")]
    mcp = session(connected("calc"), tools=tools)
    dialog = FakeDialog("1", "1", "1", "x", "9", "2", ESC, ESC, ESC, ESC)
    assert run(run_mcp_settings(dialog, context(mcp))) == DISMISSED
    text = dialog.text()
    assert "Tools for calc\n2 tools\n  1. Add — read-only\n  2. wipe — destructive\nChoose 1-2 · Esc to go back" in text
    assert ("Add [read-only]\ncalc\n\nTool name: add\nFull name: mcp__calc__add\n\nDescription:\nDoes things\n\n"
            "Parameters:\n  • a (required): number - first\n  • b: unknown\nEsc to go back") in text
    assert "Description:\nFailed to load description" in text


def test_tools_of_a_disconnected_server_and_annotations() -> None:
    from app.components.mcp.mcp_tool_list_view import select_tool, server_tools

    failed = info(FailedMCPServer("calc", STDIO))
    assert server_tools(failed, [tool("mcp__calc__x")]) == []
    dialog = FakeDialog("1", ESC)
    assert run(select_tool(dialog, failed, [tool("mcp__calc__x")])) is not None
    assert dialog.asked[0] == "Tools for calc\n0 tools\nNo tools available\nEsc to go back"
    plain = SimpleNamespace(name="mcp__calc__x")
    assert tool_display_name(plain, "calc") == "x" and tool_annotations(plain) == []
    bare = SimpleNamespace(name="mcp__calc__x", description=lambda _a: "")
    assert render_tool_detail(bare, info(connected("calc"))) == [
        "x", "calc", "", "Tool name: x", "Full name: mcp__calc__x"]


# ── the remote menu ──────────────────────────────────────────────────────────

def test_remote_menu_rendering_and_options() -> None:
    ctx = context(session())
    web = info(connected("web", HTTP), "http", False)
    assert render_remote_server(ctx, web, 1, "boom") == [
        "Web MCP Server", "", "Status: ✔ connected", "Auth: ✔ authenticated", "URL: https://mcp.example/api",
        f"Config location: {get_global_config_path()}", "Capabilities: tools", "Tools: 1 tools", "", "Error: boom"]
    needs = info(NeedsAuthMCPServer("web", HTTP), "http", False)
    assert render_remote_server(ctx, needs, 0)[2:4] == ["Status: △ needs authentication", "Auth: ✘ not authenticated"]
    assert remote_menu_options(needs, 0) == [("Authenticate", "auth"), ("Disable", "toggle-enabled")]
    assert remote_menu_options(web, 1) == [("View tools", "tools"), ("Re-authenticate", "reauth"),
                                           ("Clear authentication", "clear-auth"),
                                           ("Reconnect", "reconnectMcpServer"), ("Disable", "toggle-enabled")]
    disabled = info(DisabledMCPServer("web", HTTP), "http", True)
    assert remote_menu_options(disabled, 0) == [("Enable", "toggle-enabled"), ("Re-authenticate", "reauth"),
                                                ("Clear authentication", "clear-auth")]
    assert remote_menu_options(info(FailedMCPServer("web", HTTP), "http", False), 0) == [
        ("Authenticate", "auth"), ("Reconnect", "reconnectMcpServer"), ("Disable", "toggle-enabled")]
    assert render_remote_server(ctx, disabled, 0)[2] == "Status: ◯ disabled"
    assert render_remote_server(ctx, info(FailedMCPServer("w", HTTP), "http"), 0)[2] == "Status: ✘ failed"
    pending = SimpleNamespace(type="pending", name="w", config=HTTP)
    assert render_remote_server(ctx, info(pending, "http"), 0)[2] == "Status: ◯ connecting…"


class Flow:
    """`perform_mcp_oauth_flow`: shows the URL, waits for a pasted callback (or the abort), then finishes."""

    def __init__(self, *, error: Exception | None = None, wait: bool = True) -> None:
        self.error = error
        self.wait = wait
        self.calls: list[dict[str, Any]] = []
        self.pasted: list[str] = []

    async def __call__(self, name: str, config: Any, on_url: Any, signal: Any, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})
        on_url("https://auth.example/authorize?x=1")
        if self.wait:
            got: asyncio.Future[str] = asyncio.get_running_loop().create_future()

            def submit(url: str) -> None:
                self.pasted.append(url)
                got.set_result(url)

            kwargs["on_waiting_for_callback"](submit)
            while not got.done():
                if signal.aborted:
                    raise AuthenticationCancelledError()
                await asyncio.sleep(0.005)
        if self.error is not None:
            raise self.error


def test_authenticating_a_server_through_a_pasted_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(NeedsAuthMCPServer("web", HTTP))
    flow = Flow()
    dialog = FakeDialog("1", "1", "http://localhost:1/callback?code=abc")
    result = run(run_mcp_settings(dialog, context(mcp, flow=flow)))
    assert result == "Authentication successful. Connected to web."
    assert flow.pasted == ["http://localhost:1/callback?code=abc"]
    assert "skip_browser_open" not in flow.calls[0]  # the browser opens, as upstream's interactive flow
    assert "Authenticating with web…\n A browser window will open for authentication" in dialog.out
    assert f"{COPY_URL}\nhttps://auth.example/authorize?x=1" in dialog.out and PASTE_URL in dialog.out
    assert mcp.manager.clients[0].type == "connected"


def test_reauthenticating_revokes_first_and_says_reconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(connected("web", HTTP), tools=[tool("mcp__web__q")])
    revoked: list[Any] = []
    result = run(run_mcp_settings(FakeDialog("1", "2"), context(
        mcp, tokens={"web": {"accessToken": "t"}}, flow=Flow(wait=False), revoked=revoked)))
    assert result == "Authentication successful. Reconnected to web."
    assert revoked == [("web", {"preserve_step_up_state": True})]


@pytest.mark.parametrize("client, message", [
    (lambda n, c: ReconnectResult(client=NeedsAuthMCPServer(n, c)), STILL_NEEDS_AUTH),
    (lambda n, c: ReconnectResult(client=FailedMCPServer(n, c)), RECONNECT_FAILED),
])
def test_authenticated_but_not_connected(monkeypatch: pytest.MonkeyPatch, client: Any, message: str) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects({"web": client}))
    mcp = session(NeedsAuthMCPServer("web", HTTP))
    assert run(run_mcp_settings(FakeDialog("1", "1"), context(mcp, flow=Flow(wait=False)))) == message
    assert "TDDAgents" in message


def test_esc_aborts_the_flow_and_an_error_shows_in_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(NeedsAuthMCPServer("web", HTTP))
    # The second flow fails at once: the line typed while it ran is moot.
    dialog = FakeDialog("1", "1", ESC, "1", "moot", ESC, ESC)
    flows = [Flow(), Flow(error=RuntimeError("consent refused"), wait=False)]

    async def flow(*args: Any, **kwargs: Any) -> None:
        await flows.pop(0)(*args, **kwargs)

    assert run(run_mcp_settings(dialog, context(mcp, flow=flow))) == DISMISSED
    assert flows == []
    assert "Error: consent refused" in dialog.asked[-2] and "Error:" not in dialog.asked[2]


def test_xaa_copy_and_a_flow_finished_before_any_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    xaa = McpServerConfig(type="http", scope="user", url="https://x", oauth=McpOAuthConfig(xaa=True))
    mcp = session(NeedsAuthMCPServer("idp", xaa))
    dialog = FakeDialog("1", "1")
    assert run(run_mcp_settings(dialog, context(mcp, flow=Flow(wait=False)))) == (
        "Authentication successful. Connected to idp.")
    assert "Authenticating with idp…\n Authenticating via your identity provider" in dialog.out


def test_a_line_typed_before_the_flow_waits_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(NeedsAuthMCPServer("web", HTTP))

    async def slow_flow(name: str, config: Any, on_url: Any, signal: Any, **kwargs: Any) -> None:
        await asyncio.sleep(0.05)

    dialog = FakeDialog("1", "1", "too early")
    assert run(run_mcp_settings(dialog, context(mcp, flow=slow_flow))) == "Authentication successful. Connected to web."


def test_clearing_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup = Cleanup()
    mcp = session(connected("web", HTTP, cleanup=cleanup), tools=[tool("mcp__web__q")])
    revoked: list[Any] = []
    changes: list[int] = []
    mcp.on_change = lambda: changes.append(1)
    result = run(run_mcp_settings(FakeDialog("1", "3"), context(mcp, tokens={"web": {"a": 1}}, revoked=revoked)))
    assert result == "Authentication cleared for web."
    assert revoked == [("web", {})] and cleanup.calls == 1 and changes == [1]
    assert mcp.manager.clients == [FailedMCPServer(name="web", config=HTTP)] and mcp.manager.tools == []


def test_reconnecting_and_toggling_a_remote_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(FailedMCPServer("web", HTTP), cwd=str(tmp_path))
    dialog = FakeDialog("1", "2")
    assert run(run_mcp_settings(dialog, context(mcp, cwd=str(tmp_path)))) == "Reconnected to web."
    assert "Connecting to web…\n Establishing connection to MCP server\nThis may take a few moments." in dialog.out
    dialog = FakeDialog("1", "3", ESC)  # Disable, back to the list, Esc
    assert run(run_mcp_settings(dialog, context(mcp, cwd=str(tmp_path)))) == DISMISSED
    assert is_mcp_server_disabled("web", tmp_path)

    class Broken:
        cwd = str(tmp_path)

        def all_clients(self) -> list[Any]:
            return [FailedMCPServer("web", HTTP)]

        def all_tools(self) -> list[Any]:
            return []

        async def reconnect(self, name: str, config: Any) -> Any:
            raise RuntimeError("down")

    assert run(run_mcp_settings(FakeDialog("1", "2"), context(Broken()))) == "Error reconnecting to web: down"


def test_view_tools_from_the_remote_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp = session(connected("web", HTTP), tools=[tool("mcp__web__q")])
    dialog = FakeDialog("1", "1", ESC, ESC, ESC)
    assert run(run_mcp_settings(dialog, context(mcp))) == DISMISSED
    assert any(text.startswith("Tools for web") for text in dialog.asked)


# ── the argument forms ───────────────────────────────────────────────────────

def test_mcp_reconnect_command(monkeypatch: pytest.MonkeyPatch) -> None:
    reconnects = Reconnects({
        "auth": lambda n, c: ReconnectResult(client=NeedsAuthMCPServer(n, c)),
        "bad": lambda n, c: ReconnectResult(client=FailedMCPServer(n, c)),
    })
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", reconnects)
    mcp = session(connected("my server"), FailedMCPServer("auth", HTTP), FailedMCPServer("bad", STDIO))
    written: list[str] = []
    ctx = context(mcp)
    assert run(call(FakeDialog(), ctx, "reconnect my server")) == "Successfully reconnected to my server"
    assert run(mcp_reconnect(mcp, "auth", written.append)) == "auth requires authentication. Use /mcp to authenticate."
    assert run(mcp_reconnect(mcp, "bad", written.append)) == "Failed to reconnect to bad"
    assert run(mcp_reconnect(mcp, "ghost", written.append)) == 'MCP server "ghost" not found'
    assert written == ["Reconnecting to auth\n Establishing connection to MCP server",
                       "Reconnecting to bad\n Establishing connection to MCP server"]

    class Broken:
        def find_client(self, name: str) -> Any:
            return connected(name)

        async def reconnect(self, name: str, config: Any) -> Any:
            raise RuntimeError("kaput")

    assert run(mcp_reconnect(Broken(), "x", written.append)) == "Error: kaput"


def test_mcp_enable_and_disable_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())
    mcp = session(connected("a"), connected("b"), connected("ide"), cwd=str(tmp_path))
    ctx = context(mcp, cwd=str(tmp_path))
    assert run(call(FakeDialog(), ctx, "disable")) == "Disabled 2 MCP server(s)"
    assert [c.type for c in mcp.manager.clients] == ["disabled", "disabled", "connected"]
    assert run(call(FakeDialog(), ctx, "disable all")) == "All MCP servers are already disabled"
    assert run(call(FakeDialog(), ctx, "enable a")) == 'MCP server "a" enabled'
    assert run(mcp_toggle(mcp, "enable", "ghost")) == 'MCP server "ghost" not found'
    assert run(mcp_toggle(mcp, "enable", "ide")) == 'MCP server "ide" not found'
    # A named server is toggled whatever the action says, as upstream's `toggleMcpServer`.
    assert run(call(FakeDialog(), ctx, "enable a")) == 'MCP server "a" enabled'
    assert mcp.manager.clients[0].type == "disabled"
    assert run(call(FakeDialog(), ctx, "enable all")) == "Enabled 2 MCP server(s)"
    assert run(call(FakeDialog(), ctx, "enable")) == "All MCP servers are already enabled"
    assert run(call(FakeDialog(), ctx, "disable b")) == 'MCP server "b" disabled'


def test_the_dialog_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(session(connected("a")))
    assert run(call(FakeDialog(ESC), ctx)) == DISMISSED
    assert run(call(FakeDialog(ESC), ctx, "no-redirect")) == DISMISSED
    assert run(call(FakeDialog(ESC), ctx, "reconnect")) == DISMISSED  # no name: the dialog
    assert run(call(FakeDialog(ESC), ctx, "frobnicate")) == DISMISSED


# ── in the REPL ──────────────────────────────────────────────────────────────

def test_mcp_in_the_repl_is_answered_by_the_next_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.screens.repl import Repl
    from tests.test_repl import FakeIO

    monkeypatch.setattr("app.cli.session_mcp.reconnect_mcp_server_impl", Reconnects())

    async def go() -> tuple[FakeIO, list[Any]]:
        io = FakeIO()
        mcp = session(connected("calc"))
        mcp.settings_locations = lambda: SettingsLocations(project_root=Path("/proj"), managed_dir=Path("/etc/x"))
        interrupted: list[Any] = []
        run_ = SimpleNamespace(on_message=None, ctx=SimpleNamespace(), mcp=mcp,
                               engine=SimpleNamespace(interrupt=lambda *a: interrupted.append(a)))
        repl = Repl(run_, io)  # type: ignore[arg-type]
        repl.turn = asyncio.create_task(asyncio.sleep(10))  # /mcp is immediate: it opens mid-turn
        await repl.handle_line("/mcp")
        while repl.pending is None:
            await asyncio.sleep(0.005)
        await repl.handle_line("1")
        while "Calc MCP Server" not in io.text():
            await asyncio.sleep(0.005)
        repl.interrupt()  # Esc: back to the list, the turn untouched
        while io.text().count("Manage MCP servers") < 2:
            await asyncio.sleep(0.005)
        await repl.handle_line("1")
        while io.text().count("Calc MCP Server") < 2:
            await asyncio.sleep(0.005)
        await repl.handle_line("1")
        while "Reconnected to calc." not in io.text():
            await asyncio.sleep(0.005)
        repl.turn.cancel()
        await asyncio.gather(*repl._dialog_tasks)
        return io, interrupted

    io, interrupted = run(go())
    assert interrupted == [] and io.out[-1] == "Reconnected to calc."


def test_mcp_in_the_repl_without_servers_and_with_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.commands import SlashCommand
    from app.screens.repl import Repl
    from tests.test_repl import FakeIO

    async def go() -> list[str]:
        io = FakeIO()
        repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(), mcp=None), io)  # type: ignore[arg-type]
        await repl.run_command(SlashCommand("mcp"))
        await asyncio.gather(*repl._dialog_tasks)

        async def broken(*args: Any) -> str:
            raise RuntimeError("bad state")

        monkeypatch.setattr("app.components.mcp.mcp_command.call", broken)
        mcp = session(connected("a"))
        repl2 = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(), mcp=mcp), io)  # type: ignore[arg-type]
        await repl2.run_command(SlashCommand("mcp"))
        await asyncio.gather(*repl2._dialog_tasks)
        return io.out

    assert run(go()) == ["No MCP servers configured.", "Error: bad state"]


def test_the_panel_context_reads_the_runs_settings() -> None:
    from app.screens.repl import Repl
    from tests.test_repl import FakeIO

    mcp = session(cwd="/proj")
    mcp.settings_locations = lambda: SettingsLocations(project_root=Path("/proj"), managed_dir=Path("/etc/pol"))
    repl = Repl(SimpleNamespace(on_message=None, ctx=SimpleNamespace(), mcp=mcp), FakeIO())  # type: ignore[arg-type]
    ctx = repl._panel_context()
    assert ctx.mcp is mcp and ctx.cwd == "/proj" and ctx.managed_dir == Path("/etc/pol")
    mcp.settings_locations = None
    assert repl._panel_context().managed_dir is None


def test_the_default_token_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.components.mcp import types as mcp_types

    seen: list[Any] = []

    class Provider:
        def __init__(self, name: str, config: Any) -> None:
            seen.append(name)

        async def tokens(self) -> Any:
            return {"accessToken": "t"}

    monkeypatch.setattr(mcp_types, "ClaudeAuthProvider", Provider)
    assert run(mcp_types._stored_tokens("web", HTTP)) == {"accessToken": "t"} and seen == ["web"]
