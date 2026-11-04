"""
`/doctor`'s MCP section: each configuration scope's parse errors and warnings.

Ported from `reference/claude-code/src/components/mcp/McpParsingWarnings.tsx` -> `McpParsingWarnings`,
`McpConfigErrorSection`, `filterErrors`: the user, project, local and enterprise scopes, read once
(`getMcpConfigsByScope`, `app/services/mcp/config.py` -> `get_mcp_configs_by_scope`), each with its label
and location (`app/services/mcp/utils.py`) and its errors (`fatal`) and warnings. Lines instead of Ink
boxes; nothing when no scope has any. *Divergence:* upstream's "For help configuring MCP servers, see:"
link to Claude Code's documentation is left out, as TDDAgents has no such page.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from app.services.mcp.config import get_mcp_configs_by_scope
from app.services.mcp.types import ValidationError
from app.services.mcp.utils import describe_mcp_config_file_path, get_scope_label
from app.utils.settings.settings import SettingsLocations

TITLE = "MCP Config Diagnostics"
#: The scopes `McpParsingWarnings` reads, in its order.
SCOPES: tuple[Literal["user", "project", "local", "enterprise"], ...] = ("user", "project", "local", "enterprise")


def filter_errors(errors: Sequence[ValidationError], severity: str) -> list[ValidationError]:
    """`filterErrors`."""
    return [error for error in errors if error.severity == severity]


def _entry(kind: str, error: ValidationError) -> str:
    server = f"[{error.server_name}] " if error.server_name else ""
    where = f"{error.path}: " if error.path else ""
    return f" └ [{kind}] {server}{where}{error.message}"


def mcp_config_error_section(scope: str, cwd: str | Path, managed_dir: Path | None,
                             errors: Sequence[ValidationError]) -> list[str]:
    """`McpConfigErrorSection`: the scope's label, location, errors and warnings; nothing without any."""
    fatal, warnings = filter_errors(errors, "fatal"), filter_errors(errors, "warning")
    if not fatal and not warnings:
        return []
    status = "Failed to parse" if fatal else "Contains warnings"
    return ["", f"[{status}] {get_scope_label(scope)}",
            f"Location: {describe_mcp_config_file_path(scope, cwd, managed_dir)}",
            *(_entry("Error", e) for e in fatal), *(_entry("Warning", w) for w in warnings)]


def mcp_parsing_warnings(locations: SettingsLocations) -> list[str]:
    """`McpParsingWarnings`: the section's lines, or none when no scope has an error or a warning."""
    cwd = locations.project_root
    sections: list[str] = []
    for scope in SCOPES:
        _, errors = get_mcp_configs_by_scope(scope, cwd, locations)
        sections += mcp_config_error_section(scope, cwd, locations.managed_dir, errors)
    if not sections:
        return []
    return ["", TITLE, *sections, ""]


__all__ = ["SCOPES", "TITLE", "filter_errors", "mcp_config_error_section", "mcp_parsing_warnings"]
