"""
The `/doctor` screen: the installation, the settings' and MCP configs' errors, the bounded environment
variables, the agent and plugin errors, unreachable permission rules and context usage warnings.

Ported from `reference/claude-code/src/screens/Doctor.tsx` -> `Doctor`, section by section, as lines (the
REPL prints them, `app/components/design_system/dialog.py`), then upstream's "Press Enter to continue…":
any line or Esc dismisses it, and its result is "TDDAgents diagnostics dismissed" (upstream's
"Claude Code diagnostics dismissed", the product substitution). What it reads:

* **Diagnostics**: `app/utils/doctor_diagnostic.py` -> `get_doctor_diagnostic`, its configuration
  warnings, then **Invalid Settings**: the settings errors that are not an MCP config's
  (`useSettingsErrors`, `app/utils/settings/all_errors.py`), drawn by `ValidationErrorsList`
  (`app/components/validation_errors_list.py`);
* **Sandbox**: `app/components/sandbox/sandbox_doctor_section.py` (the run's sandbox adapter, else one over
  the settings);
* **MCP Config Diagnostics**: `app/components/mcp/mcp_parsing_warnings.py`;
* **Environment Variables**: `BASH_MAX_OUTPUT_LENGTH`, `TASK_MAX_OUTPUT_LENGTH` and
  `TDDAGENTS_MAX_OUTPUT_TOKENS` (upstream's `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, checked against the bounds of
  upstream's latest model, as upstream), each listed when its value is not taken as given
  (`app/utils/env_validation.py`);
* **Agent Parse Errors**: `app/tools/agent_tool/load_agents_dir.py` -> `get_failed_agent_files`;
* **Plugin Errors**: `appState.plugins.errors` (`getPluginErrorMessage`);
* **Unreachable Permission Rules** and **Context Usage Warnings**: `app/utils/doctor_context_warnings.py`.

Not ported, having no counterpart here: the package manager line, the multiple-installations warning and
the auto-updater's recommendation; the config install method; the **Updates** section (auto-updates,
update permissions, channel, dist-tags: TDDAgents does not update itself); `KeybindingWarnings` (no keybindings
file); **Version Locks** (no native installer).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.components.design_system.dialog import CANCELLED, DialogIO
from app.components.mcp.mcp_parsing_warnings import mcp_parsing_warnings
from app.components.sandbox.sandbox_doctor_section import sandbox_doctor_section
from app.components.validation_errors_list import render_validation_errors
from app.tool import Tool
from app.types.permissions import ToolPermissionContext
from app.types.plugin import PluginError, get_plugin_error_message
from app.utils.analyze_context import TokenCounter, rough_count_tokens
from app.utils.claudemd import MemoryFileInfo
from app.utils.doctor_context_warnings import ContextWarning, ContextWarnings, check_context_warnings
from app.utils.doctor_diagnostic import DiagnosticInfo, get_doctor_diagnostic
from app.utils.env_validation import validate_bounded_int_env_var
from app.utils.settings.all_errors import get_settings_with_all_errors, is_mcp_error
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.validation import ValidationError
from app.utils.shell.output_limits import BASH_MAX_OUTPUT_DEFAULT, BASH_MAX_OUTPUT_UPPER_LIMIT
from app.utils.task.disk_output import TASK_MAX_OUTPUT_DEFAULT, TASK_MAX_OUTPUT_UPPER_LIMIT
from app.utils.tokens import get_model_max_output_tokens

#: `figures.warning`.
WARNING = "⚠"
#: `<PressEnterToContinue />`.
PRESS_ENTER_TO_CONTINUE = "Press Enter to continue…"
#: `handleDismiss`' result.
DISMISSED = "TDDAgents diagnostics dismissed"
#: The model whose output-token bounds `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is checked against, as upstream
#: ("Check for values against the latest supported model").
MAX_OUTPUT_TOKENS_REFERENCE_MODEL = "claude-opus-4-6"
#: Upstream's `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (`app/utils/tokens.py` -> `get_max_output_tokens_for_model`).
MAX_OUTPUT_TOKENS_ENV = "TDDAGENTS_MAX_OUTPUT_TOKENS"


@dataclass(frozen=True, slots=True)
class DoctorContext:
    """What the screen reads from the session (upstream's `useAppState` selectors and module state)."""

    locations: SettingsLocations | None
    workspace: Any = None
    tools: Sequence[Tool] = ()
    active_agents: Iterable[Any] | None = None
    permission_context: ToolPermissionContext = field(default_factory=ToolPermissionContext)
    plugin_errors: Sequence[PluginError] = ()
    count_tokens: TokenCounter = rough_count_tokens
    #: The session's memory files (`getMemoryFiles`).
    memory_files: Callable[[], Sequence[MemoryFileInfo]] = tuple
    #: `agentDefinitions.failedFiles`.
    failed_agent_files: Callable[[], Sequence[tuple[str, str]]] = tuple
    environ: Any = field(default_factory=lambda: os.environ)
    #: The run's `SandboxAdapter`; `None` builds one over `locations`.
    sandbox: Any = None


def environment_variable_errors(environ: Any) -> list[tuple[str, str, str]]:
    """`envValidationErrors`: each `(name, status, message)` whose value is capped or invalid."""
    max_tokens_default, max_tokens_upper = get_model_max_output_tokens(MAX_OUTPUT_TOKENS_REFERENCE_MODEL)
    checks = (
        ("BASH_MAX_OUTPUT_LENGTH", BASH_MAX_OUTPUT_DEFAULT, BASH_MAX_OUTPUT_UPPER_LIMIT),
        ("TASK_MAX_OUTPUT_LENGTH", TASK_MAX_OUTPUT_DEFAULT, TASK_MAX_OUTPUT_UPPER_LIMIT),
        (MAX_OUTPUT_TOKENS_ENV, max_tokens_default, max_tokens_upper),
    )
    errors: list[tuple[str, str, str]] = []
    for name, default, upper in checks:
        result = validate_bounded_int_env_var(name, environ.get(name), default, upper)
        if result.status != "valid":
            errors.append((name, result.status, result.message or ""))
    return errors


def diagnostics_section(diagnostic: DiagnosticInfo, settings_errors: Sequence[ValidationError]) -> list[str]:
    """The **Diagnostics** box: the installation, its configuration warnings, then the invalid settings."""
    rg = diagnostic.ripgrep_status
    mode = "vendor" if rg.mode == "builtin" else (rg.system_path or "system")
    lines = [
        "Diagnostics",
        f"└ Currently running: {diagnostic.installation_type} ({diagnostic.version})",
        f"└ Path: {diagnostic.installation_path}",
        f"└ Invoked: {diagnostic.invoked_binary}",
        f"└ Search: {'OK' if rg.working else 'Not working'} ({mode})",
    ]
    if diagnostic.warnings:
        lines.append("")
        for warning in diagnostic.warnings:
            lines += [f"Warning: {warning.issue}", f"Fix: {warning.fix}"]
    if settings_errors:
        lines += ["", "Invalid Settings", *render_validation_errors(settings_errors), ""]
    return lines


def environment_variables_section(errors: Sequence[tuple[str, str, str]]) -> list[str]:
    """**Environment Variables**."""
    if not errors:
        return []
    return ["Environment Variables", *(f"└ {name}: {message}" for name, _status, message in errors)]


def agent_parse_errors_section(failed: Sequence[tuple[str, str]]) -> list[str]:
    """**Agent Parse Errors**."""
    if not failed:
        return []
    return ["Agent Parse Errors", f"└ Failed to parse {len(failed)} agent file(s):",
            *(f"  └ {path}: {error}" for path, error in failed)]


def plugin_errors_section(errors: Sequence[PluginError]) -> list[str]:
    """**Plugin Errors**."""
    if not errors:
        return []
    lines = ["Plugin Errors", f"└ {len(errors)} plugin error(s) detected:"]
    for error in errors:
        entry: dict[str, Any] = dict(error)
        plugin = f" [{entry['plugin']}]" if entry.get("plugin") else ""
        lines.append(f"  └ {entry.get('source') or 'unknown'}{plugin}: {get_plugin_error_message(error)}")
    return lines


def unreachable_rules_section(warning: ContextWarning | None) -> list[str]:
    """**Unreachable Permission Rules**."""
    if warning is None:
        return []
    return ["Unreachable Permission Rules", f"└ {WARNING} {warning.message}",
            *(f"  └ {detail}" for detail in warning.details)]


def context_usage_section(warnings: ContextWarnings) -> list[str]:
    """**Context Usage Warnings**: the memory files, the agents, the MCP servers."""
    parts = ((warnings.claude_md_warning, "Files:"), (warnings.agent_warning, "Top contributors:"),
             (warnings.mcp_warning, "MCP servers:"))
    if not any(warning is not None for warning, _ in parts):
        return []
    lines = ["Context Usage Warnings"]
    for warning, heading in parts:
        if warning is not None:
            lines += [f"└ {WARNING} {warning.message}", f"  └ {heading}",
                      *(f"    └ {detail}" for detail in warning.details)]
    return lines


async def doctor_lines(context: DoctorContext) -> list[str]:
    """`Doctor`'s screen, every section in upstream's order."""
    all_errors = get_settings_with_all_errors(context.locations) if context.locations is not None else []
    settings_errors = [error for error in all_errors if not is_mcp_error(error)]
    diagnostic = get_doctor_diagnostic(context.locations, context.workspace)
    warnings = await check_context_warnings(context.tools, context.active_agents, context.permission_context,
                                            context.memory_files(), context.count_tokens)
    sandbox = context.sandbox
    if sandbox is None and context.locations is not None:
        from app.utils.sandbox.sandbox_adapter import SandboxAdapter

        sandbox = SandboxAdapter(context.locations)
    return [
        *diagnostics_section(diagnostic, settings_errors),
        *sandbox_doctor_section(sandbox),
        *(mcp_parsing_warnings(context.locations) if context.locations is not None else []),
        *environment_variables_section(environment_variable_errors(context.environ)),
        *agent_parse_errors_section(context.failed_agent_files()),
        *plugin_errors_section(context.plugin_errors),
        *unreachable_rules_section(warnings.unreachable_rules_warning),
        *context_usage_section(warnings),
    ]


async def doctor(io: DialogIO, context: DoctorContext,
                 lines: Callable[[DoctorContext], Awaitable[list[str]]] = doctor_lines) -> str:
    """`<Doctor onDone>`: the screen until Enter or Esc, then its result."""
    io.write("Checking installation status…")
    screen = await lines(context)
    await io.ask("\n".join([*screen, "", PRESS_ENTER_TO_CONTINUE]), lambda _line: True, PRESS_ENTER_TO_CONTINUE,
                 CANCELLED)
    return DISMISSED


__all__ = [
    "DISMISSED",
    "MAX_OUTPUT_TOKENS_ENV",
    "MAX_OUTPUT_TOKENS_REFERENCE_MODEL",
    "PRESS_ENTER_TO_CONTINUE",
    "WARNING",
    "DoctorContext",
    "agent_parse_errors_section",
    "context_usage_section",
    "diagnostics_section",
    "doctor",
    "doctor_lines",
    "environment_variable_errors",
    "environment_variables_section",
    "plugin_errors_section",
    "unreachable_rules_section",
]
