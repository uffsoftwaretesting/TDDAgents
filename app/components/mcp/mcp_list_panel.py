"""
`/mcp`'s first view: every MCP server, grouped by where it is configured, with its status.

Ported from `reference/claude-code/src/components/mcp/MCPListPanel.tsx` -> `MCPListPanel`,
`SCOPE_ORDER`, `getScopeHeading`, `groupServersByScope`, and its status icons and texts. The rows
are numbered in upstream's display order (project, local, user, enterprise, then the built-in
`dynamic` servers last); picking a number opens that server's menu, Esc closes the dialog with
`MCP dialog dismissed`.

Not ported: the claude.ai connectors section and the agent-only servers (`Agent MCPs`, agents'
`mcpServers`), which TDDAgents has neither of; `McpParsingWarnings`; and the footer's
`--debug` hint and documentation link, which point at Claude Code (TDDAgents writes its log to
the session's `metrics_and_logging/execution_logs.txt`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.components.design_system.dialog import CANCELLED, Cancelled, DialogIO, choice_hint
from app.components.mcp.types import ServerInfo
from app.services.mcp.utils import describe_mcp_config_file_path
from app.utils.string_utils import plural

#: `SCOPE_ORDER`; `dynamic` (built-in) is listed separately, last.
SCOPE_ORDER = ("project", "local", "user", "enterprise")
DISMISSED = "MCP dialog dismissed"
#: `figures`.
TICK, CROSS, RADIO_OFF, TRIANGLE_UP_OUTLINE = "✔", "✘", "◯", "△"


def get_scope_heading(scope: str, cwd: str | Path, managed_dir: str | Path | None = None) -> tuple[str, str | None]:
    """`getScopeHeading`: the group's label, and the file it comes from."""
    if scope == "project":
        return "Project MCPs", describe_mcp_config_file_path(scope, cwd)
    if scope == "user":
        return "User MCPs", describe_mcp_config_file_path(scope, cwd)
    if scope == "local":
        return "Local MCPs", describe_mcp_config_file_path(scope, cwd)
    if scope == "enterprise":
        return "Enterprise MCPs", None
    if scope == "dynamic":
        return "Built-in MCPs", "always available"
    return scope, None


def group_servers_by_scope(servers: Sequence[ServerInfo]) -> dict[str, list[ServerInfo]]:
    """`groupServersByScope`: each scope's servers, alphabetically."""
    groups: dict[str, list[ServerInfo]] = {}
    for server in servers:
        groups.setdefault(server.scope, []).append(server)
    return {scope: sorted(group, key=lambda s: s.name) for scope, group in groups.items()}


def selectable_servers(servers: Sequence[ServerInfo]) -> list[ServerInfo]:
    """The rows in display order: the scopes of `SCOPE_ORDER`, then the built-in servers."""
    groups = group_servers_by_scope(servers)
    return [server for scope in (*SCOPE_ORDER, "dynamic") for server in groups.get(scope, [])]


def server_status(client: object) -> str:
    """A row's status icon and text."""
    kind = getattr(client, "type", None)
    if kind == "disabled":
        return f"{RADIO_OFF} disabled"
    if kind == "connected":
        return f"{TICK} connected"
    if kind == "pending":
        attempt, maximum = getattr(client, "reconnect_attempt", None), getattr(client, "max_reconnect_attempts", None)
        if attempt and maximum:
            return f"{RADIO_OFF} reconnecting ({attempt}/{maximum})…"
        return f"{RADIO_OFF} connecting…"
    if kind == "needs-auth":
        return f"{TRIANGLE_UP_OUTLINE} needs authentication"
    return f"{CROSS} failed"


def render_list_panel(
    servers: Sequence[ServerInfo], cwd: str | Path, managed_dir: str | Path | None = None
) -> list[str]:
    """`Manage MCP servers`, the server count, and each scope's heading and numbered rows."""
    rows = selectable_servers(servers)
    groups = group_servers_by_scope(servers)
    lines = ["Manage MCP servers", f"{len(servers)} {plural(len(servers), 'server')}"]
    for scope in (*SCOPE_ORDER, "dynamic"):
        group = groups.get(scope)
        if not group:
            continue
        label, path = get_scope_heading(scope, cwd, managed_dir)
        lines += ["", f"  {label}" + (f" ({path})" if path else "")]
        lines += [f"  {rows.index(server) + 1}. {server.name} · {server_status(server.client)}" for server in group]
    return lines


async def select_server(
    io: DialogIO, servers: Sequence[ServerInfo], cwd: str | Path, managed_dir: str | Path | None = None
) -> ServerInfo | Cancelled:
    """The list, then the server picked, or `CANCELLED` on Esc."""
    rows = selectable_servers(servers)
    text = "\n".join([*render_list_panel(servers, cwd, managed_dir), choice_hint(len(rows), "cancel")])

    def parse(line: str) -> int | None:
        stripped = line.strip()
        return int(stripped) - 1 if stripped.isdigit() and 1 <= int(stripped) <= len(rows) else None

    index: int | Cancelled = await io.ask(text, parse, f"Choose 1-{len(rows)}.", CANCELLED)
    return index if isinstance(index, Cancelled) else rows[index]


__all__ = [
    "DISMISSED",
    "SCOPE_ORDER",
    "get_scope_heading",
    "group_servers_by_scope",
    "render_list_panel",
    "select_server",
    "selectable_servers",
    "server_status",
]
