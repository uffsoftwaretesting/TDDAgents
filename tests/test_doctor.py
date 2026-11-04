"""
`/doctor` (`reference/claude-code/src/commands/doctor/`, `screens/Doctor.tsx`, `utils/doctorDiagnostic.ts`,
`utils/doctorContextWarnings.ts`, `utils/envValidation.ts`, `utils/statusNoticeHelpers.ts`,
`utils/permissions/shadowedRuleDetection.ts`, `components/mcp/McpParsingWarnings.tsx`): its registration,
each section, and the screen.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

import app.utils.doctor_context_warnings as dcw
from app.commands.commands import (
    HELP_TEXT,
    SlashCommand,
    base_help_text,
    built_in_command_names,
    get_commands,
    help_text,
    parse_slash_command,
)
from app.commands.doctor.doctor import DESCRIPTION, call, is_enabled
from app.components.mcp.mcp_parsing_warnings import mcp_config_error_section, mcp_parsing_warnings
from app.screens.doctor import (
    DISMISSED,
    PRESS_ENTER_TO_CONTINUE,
    DoctorContext,
    agent_parse_errors_section,
    context_usage_section,
    diagnostics_section,
    doctor,
    doctor_lines,
    environment_variable_errors,
    environment_variables_section,
    plugin_errors_section,
    unreachable_rules_section,
)
from app.services.mcp.types import ValidationError as McpValidationError
from app.services.mcp.utils import get_scope_label
from app.tool import build_tool
from app.tools.agent_tool.load_agents_dir import AgentDefinition, get_failed_agent_files, get_parse_error
from app.types.permissions import (
    PermissionBehavior,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)
from app.utils.claudemd import MAX_MEMORY_CHARACTER_COUNT, MemoryFileInfo
from app.utils.doctor_context_warnings import (
    ContextWarning,
    ContextWarnings,
    check_agent_descriptions,
    check_claude_md_files,
    check_context_warnings,
    check_mcp_tools,
    check_unreachable_rules,
)
from app.utils.doctor_diagnostic import (
    INSTALLATION_ROOT,
    ConfigurationWarning,
    DiagnosticInfo,
    RipgrepStatus,
    detect_configuration_issues,
    get_doctor_diagnostic,
    get_invoked_binary,
)
from app.utils.env_validation import EnvVarValidationResult, validate_bounded_int_env_var
from app.utils.permissions.shadowed_rule_detection import (
    detect_unreachable_rules,
    is_shared_setting_source,
)
from app.utils.ripgrep import get_ripgrep_status
from app.utils.settings.settings import SettingsLocations
from app.utils.settings.validation import ValidationError
from app.utils.status_notice_helpers import AGENT_DESCRIPTIONS_THRESHOLD, get_agent_descriptions_total_tokens
from tests.dialog_fakes import ESC, FakeDialog


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _loc(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "project", home=tmp_path / "home",
                             managed_dir=tmp_path / "managed")


def _write(path: Path, content: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def _agent(name: str, description: str, source: str = "user") -> AgentDefinition:
    return AgentDefinition(name=name, description=description, prompt="", source=source)


def _rule(behavior: PermissionBehavior, tool: str, content: str | None = None,
          source: PermissionRuleSource = PermissionRuleSource.USER_SETTINGS) -> PermissionRule:
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content, source=source)


ALLOW, ASK, DENY = PermissionBehavior.ALLOW, PermissionBehavior.ASK, PermissionBehavior.DENY


# ── registration ─────────────────────────────────────────────────────────────

def test_doctor_is_a_local_command_that_waits_for_the_turn(monkeypatch):
    monkeypatch.delenv("DISABLE_DOCTOR_COMMAND", raising=False)
    doctor_command = next(c for c in get_commands(None) if c.name == "doctor")
    assert (doctor_command.type, doctor_command.description) == (
        "local", "Diagnose and verify your TDDAgents installation and settings") == ("local", DESCRIPTION)
    assert parse_slash_command(" /doctor ") == SlashCommand("doctor")
    assert not SlashCommand("doctor").immediate
    assert "  /doctor                       diagnose and verify your TDDAgents installation and settings\n" in (
        help_text(get_commands(None)))
    assert base_help_text() == HELP_TEXT


@pytest.mark.parametrize("value", ["1", "true", " Yes ", "on"])
def test_disable_doctor_command_turns_it_off(monkeypatch, value):
    monkeypatch.setenv("DISABLE_DOCTOR_COMMAND", value)
    assert not is_enabled()
    assert "doctor" not in {c.name for c in get_commands(None)}
    assert parse_slash_command("/doctor") is None  # a message to the agents
    assert "/doctor" not in help_text(get_commands(None)) and "/hooks" in base_help_text()
    assert base_help_text() == HELP_TEXT.replace(
        "  /doctor                       diagnose and verify your TDDAgents installation and settings\n", "")
    assert "doctor" in built_in_command_names()  # `builtInCommandNames` lists every built-in


@pytest.mark.parametrize("value", [None, "", "0", "false", "off"])
def test_doctor_is_on_otherwise(value):
    assert is_enabled({} if value is None else {"DISABLE_DOCTOR_COMMAND": value})


# ── envValidation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, EnvVarValidationResult(100, "valid")),
    ("", EnvVarValidationResult(100, "valid")),
    ("50", EnvVarValidationResult(50, "valid")),
    (" 70abc", EnvVarValidationResult(70, "valid")),  # parseInt reads the leading digits
    ("200", EnvVarValidationResult(200, "valid")),
    ("201", EnvVarValidationResult(200, "capped", "Capped from 201 to 200")),
    ("0", EnvVarValidationResult(100, "invalid", 'Invalid value "0" (using default: 100)')),
    ("-5", EnvVarValidationResult(100, "invalid", 'Invalid value "-5" (using default: 100)')),
    ("abc", EnvVarValidationResult(100, "invalid", 'Invalid value "abc" (using default: 100)')),
])
def test_validate_bounded_int_env_var(value, expected):
    assert validate_bounded_int_env_var("X", value, 100, 200) == expected


def test_environment_variable_errors_lists_the_capped_and_invalid():
    environ = {"BASH_MAX_OUTPUT_LENGTH": "999999", "TASK_MAX_OUTPUT_LENGTH": "1000",
               "TDDAGENTS_MAX_OUTPUT_TOKENS": "lots"}
    assert environment_variable_errors(environ) == [
        ("BASH_MAX_OUTPUT_LENGTH", "capped", "Capped from 999999 to 150000"),
        ("TDDAGENTS_MAX_OUTPUT_TOKENS", "invalid", 'Invalid value "lots" (using default: 64000)')]
    # Checked against upstream's latest model (claude-opus-4-6: 64000 by default, up to 128000).
    assert environment_variable_errors({"TDDAGENTS_MAX_OUTPUT_TOKENS": "128001"}) == [
        ("TDDAGENTS_MAX_OUTPUT_TOKENS", "capped", "Capped from 128001 to 128000")]
    assert environment_variable_errors({"TASK_MAX_OUTPUT_LENGTH": "160001"}) == [
        ("TASK_MAX_OUTPUT_LENGTH", "capped", "Capped from 160001 to 160000")]
    assert environment_variable_errors({}) == []
    assert environment_variables_section([("A", "capped", "Capped from 2 to 1")]) == [
        "Environment Variables", "└ A: Capped from 2 to 1"]
    assert environment_variables_section([]) == []


# ── shadowedRuleDetection ────────────────────────────────────────────────────

def test_a_tool_wide_deny_blocks_a_specific_allow_before_any_ask():
    allow = _rule(ALLOW, "Bash", "npm test", PermissionRuleSource.LOCAL_SETTINGS)
    deny = _rule(DENY, "Bash", source=PermissionRuleSource.PROJECT_SETTINGS)
    context = ToolPermissionContext(always_allow_rules=(allow, _rule(ALLOW, "Read")),
                                    always_deny_rules=(_rule(DENY, "Bash", "rm"), deny),
                                    always_ask_rules=(_rule(ASK, "Bash"),))
    (found,) = detect_unreachable_rules(context)
    assert (found.rule, found.shadowed_by, found.shadow_type) == (allow, deny, "deny")
    assert found.reason == 'Blocked by "Bash" deny rule (from shared project settings)'
    assert found.fix == ('Remove the "Bash" deny rule from shared project settings, or remove the specific allow '
                         "rule from project local settings")


def test_a_tool_wide_ask_shadows_a_specific_allow():
    allow = _rule(ALLOW, "Edit", "src/**")
    ask = _rule(ASK, "Edit", source=PermissionRuleSource.POLICY_SETTINGS)
    context = ToolPermissionContext(always_allow_rules=(allow,), always_ask_rules=(_rule(ASK, "Write"), ask),
                                    always_deny_rules=(_rule(DENY, "Edit", "secret"),))
    (found,) = detect_unreachable_rules(context)
    assert (found.shadowed_by, found.shadow_type) == (ask, "ask")
    assert found.reason == 'Shadowed by "Edit" ask rule (from enterprise managed settings)'
    assert found.fix == ('Remove the "Edit" ask rule from enterprise managed settings, or remove the specific allow '
                         "rule from user settings")
    # A tool-wide allow rule is never "shadowed".
    assert detect_unreachable_rules(ToolPermissionContext(always_allow_rules=(_rule(ALLOW, "Edit"),),
                                                          always_ask_rules=(ask,))) == []


def test_the_sandbox_exception_is_for_a_personal_bash_ask_rule_only():
    allow = _rule(ALLOW, "Bash", "ls")
    personal = ToolPermissionContext(always_allow_rules=(allow,), always_ask_rules=(_rule(ASK, "Bash"),))
    shared = ToolPermissionContext(always_allow_rules=(allow,), always_ask_rules=(
        _rule(ASK, "Bash", source=PermissionRuleSource.PROJECT_SETTINGS),))
    assert detect_unreachable_rules(personal, sandbox_auto_allow_enabled=True) == []
    assert len(detect_unreachable_rules(personal)) == 1
    assert len(detect_unreachable_rules(shared, sandbox_auto_allow_enabled=True)) == 1
    other = ToolPermissionContext(always_allow_rules=(_rule(ALLOW, "Edit", "x"),),
                                  always_ask_rules=(_rule(ASK, "Edit"),))
    assert len(detect_unreachable_rules(other, sandbox_auto_allow_enabled=True)) == 1


def test_shared_setting_sources():
    shared = {s for s in PermissionRuleSource if is_shared_setting_source(s)}
    assert shared == {PermissionRuleSource.PROJECT_SETTINGS, PermissionRuleSource.POLICY_SETTINGS,
                      PermissionRuleSource.COMMAND}


# ── doctorContextWarnings ────────────────────────────────────────────────────

def _memory(path: str, size: int) -> MemoryFileInfo:
    return MemoryFileInfo(path=path, type="Project",
                          content="x" * size)


def test_large_memory_files():
    limit = MAX_MEMORY_CHARACTER_COUNT
    assert check_claude_md_files([_memory("/a", limit)]) is None
    one = check_claude_md_files([_memory("/a", limit), _memory("/b", limit + 1)])
    assert one == ContextWarning("claudemd_files", "warning",
                                 f"Large TDDAGENTS.md file detected ({limit + 1:,} chars > {limit:,})",
                                 (f"/b: {limit + 1:,} chars",), 1, limit)
    two = check_claude_md_files([_memory("/small", limit + 1), _memory("/big", limit + 900)])
    assert two is not None and two.message == f"2 large TDDAGENTS.md files detected (each > {limit:,} chars)"
    assert two.details == (f"/big: {limit + 900:,} chars", f"/small: {limit + 1:,} chars")
    assert two.current_value == 2


def test_large_agent_descriptions_name_the_top_five():
    agents = [_agent(f"a{i}", "d" * (12_000 + i * 4)) for i in range(7)] + [_agent("t", "x" * 99_999, "built-in")]
    total = get_agent_descriptions_total_tokens(agents)
    assert total == sum(round(len(f"a{i}: " + "d" * (12_000 + i * 4)) / 4) for i in range(7))
    warning = check_agent_descriptions(agents)
    assert warning is not None
    assert warning.message == f"Large agent descriptions (~{total:,} tokens > {AGENT_DESCRIPTIONS_THRESHOLD:,})"
    assert warning.details == ("a6: ~3,007 tokens", "a5: ~3,006 tokens", "a4: ~3,005 tokens", "a3: ~3,004 tokens",
                               "a2: ~3,003 tokens", "(2 more custom agents)")
    assert (warning.current_value, warning.threshold) == (total, 15_000)
    assert check_agent_descriptions([_agent("a", "d" * (60_000 - 3))]) is None  # exactly the threshold
    assert check_agent_descriptions([_agent("a", "d" * 60_000)]) is not None
    assert check_agent_descriptions(None) is None and get_agent_descriptions_total_tokens(None) == 0


def _tool(name: str, prompt: str = "p", *, is_mcp: bool = False) -> Any:
    def never(args: dict[str, Any], ctx: Any) -> Any:
        raise AssertionError("no tool runs")

    return build_tool(name=name, prompt=prompt, call=never, is_mcp=is_mcp)


def _counter(total: int) -> Any:
    async def count(messages: Any, tools: Sequence[Mapping[str, Any]]) -> int | None:
        return total if tools else 0

    return count


def test_large_mcp_tools_context_by_server(monkeypatch):
    tools = [_tool("Bash")] + [_tool(f"mcp__s{i}__t", "x" * (100 * (i + 1)), is_mcp=True) for i in range(7)] + [
        _tool("mcp__s5__u", "x" * 600, is_mcp=True)]
    assert run(check_mcp_tools([_tool("Bash")], _counter(10**6))) is None
    assert run(check_mcp_tools(tools, _counter(25_000 + 500))) is None  # 25,000 after the preamble
    warning = run(check_mcp_tools(tools, _counter(60_500)))
    assert warning is not None and warning.message == "Large MCP tools context (~60,000 tokens > 25,000)"
    assert warning.details[0].startswith("s5: 2 tools (~") and warning.details[-1] == "(2 more servers)"
    assert len(warning.details) == 6 and warning.current_value == 60_000

    async def broken(tools: Any, count_tokens: Any) -> Any:
        raise RuntimeError("no counter")

    monkeypatch.setattr(dcw, "count_mcp_tool_tokens", broken)
    assert run(check_mcp_tools(tools, _counter(0))) is None  # the estimate is small
    many = [_tool(f"mcp__s__t{i}", is_mcp=True) for i in range(100_000)]
    estimated = run(check_mcp_tools(many, _counter(0)))
    assert estimated is not None and estimated.details == ("100000 MCP tools detected (token count estimated)",)
    assert estimated.message.startswith("Large MCP tools context (~") and "tokens estimated > 25,000)" in (
        estimated.message)


def test_unreachable_rules_warning():
    assert check_unreachable_rules(ToolPermissionContext()) is None
    context = ToolPermissionContext(always_allow_rules=(_rule(ALLOW, "Bash", "a(b)"), _rule(ALLOW, "Bash", "c")),
                                    always_deny_rules=(_rule(DENY, "Bash"),))
    warning = check_unreachable_rules(context)
    assert warning is not None and warning.message == "2 unreachable permission rules detected"
    assert warning.details[0] == 'Bash(a\\(b\\)): Blocked by "Bash" deny rule (from user settings)'
    assert warning.details[1].startswith('  Fix: Remove the "Bash" deny rule from user settings')
    single = ToolPermissionContext(always_allow_rules=(_rule(ALLOW, "Bash", "c"),),
                                   always_deny_rules=(_rule(DENY, "Bash"),))
    one = check_unreachable_rules(single)
    assert one is not None and (one.message, one.current_value, one.threshold) == (
        "1 unreachable permission rule detected", 1, 0)


def test_check_context_warnings_runs_every_check():
    warnings = run(check_context_warnings([], [], ToolPermissionContext(), [], _counter(0)))
    assert warnings == ContextWarnings()


# ── doctorDiagnostic ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, issue, fix", [
    ({"a": 1}, "an invalid value (expected true or an array, got object)", "Set it to true, or an array of: "
     "skills, agents, hooks, mcp."),
    (None, "an invalid value (expected true or an array, got object)", "silently ignored (schema .catch"),
    ("skills", "an invalid value (expected true or an array, got string)", "an array of: skills"),
    (3, "an invalid value (expected true or an array, got number)", "an array of: skills"),
    (["skills", "commands", 4, "themes"], "2 value(s) this client doesn't recognize: commands, themes",
     "Known surfaces for this version: skills, agents, hooks, mcp. Either remove them, or this client is older"),
])
def test_the_policy_strict_plugin_only_customization_check(tmp_path, value, issue, fix):
    _write(tmp_path / "managed-settings.json", {"strictPluginOnlyCustomization": value})
    (warning,) = detect_configuration_issues(tmp_path)
    assert warning.issue == f"managed-settings.json: strictPluginOnlyCustomization has {issue}"
    assert fix in warning.fix


@pytest.mark.parametrize("content", [{"strictPluginOnlyCustomization": True}, {"strictPluginOnlyCustomization": False},
                                     {"strictPluginOnlyCustomization": ["skills", "mcp"]}, {}, [1], "{not json"])
def test_no_policy_warning(tmp_path, content):
    _write(tmp_path / "managed-settings.json", content)
    assert detect_configuration_issues(tmp_path) == []
    assert detect_configuration_issues(tmp_path / "missing") == []


def test_the_diagnostic(tmp_path, monkeypatch):
    import app.utils.ripgrep as ripgrep

    loc = _loc(tmp_path)
    _write(loc.managed_dir / "managed-settings.json", {"strictPluginOnlyCustomization": "x"})
    monkeypatch.delenv("USE_BUILTIN_RIPGREP", raising=False)

    class Ws:
        kind = "local"

    ws = Ws()
    diagnostic = get_doctor_diagnostic(loc, ws)
    assert (diagnostic.installation_type, diagnostic.version) == ("development", "unknown")
    assert diagnostic.installation_path == str(INSTALLATION_ROOT) and (INSTALLATION_ROOT / "app" / "main.py").is_file()
    assert diagnostic.invoked_binary == get_invoked_binary() == (sys.argv[0] or "unknown")
    assert diagnostic.ripgrep_status == RipgrepStatus(working=True, mode="builtin", system_path=None)
    assert len(diagnostic.warnings) == 1
    assert get_doctor_diagnostic(None, ws).warnings == ()
    ripgrep._status[ws] = False
    assert get_doctor_diagnostic(None, ws).ripgrep_status.working is False
    monkeypatch.setenv("USE_BUILTIN_RIPGREP", "0")
    assert get_ripgrep_status(ws) == ("system", "rg", False)
    assert get_doctor_diagnostic(None, ws).ripgrep_status == RipgrepStatus(False, "system", "rg")
    monkeypatch.setattr(sys, "argv", [""])
    assert get_invoked_binary() == "unknown"


def test_a_failed_ripgrep_probe_is_recorded(monkeypatch):
    import app.utils.ripgrep as ripgrep

    class Result:
        exit_code, stdout = 127, ""

    class Ws:
        kind = "local"

        def execute(self, command: str, timeout: int | None = None) -> Any:
            return Result()

    ws = Ws()
    assert get_ripgrep_status(ws)[2] is None
    with pytest.raises(ripgrep.RipgrepUnavailableError):
        ripgrep.ensure_ripgrep(ws)
    assert get_ripgrep_status(ws)[2] is False
    Result.exit_code, Result.stdout = 0, "ripgrep 15.1.0"
    ripgrep.ensure_ripgrep(ws)
    assert get_ripgrep_status(ws)[2] is True


# ── the agents' parse errors ─────────────────────────────────────────────────

def test_failed_agent_files(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    user_agents, project_agents = home / ".tddagents" / "agents", project / ".tddagents" / "agents"
    _write(user_agents / "ok.md", "---\nname: ok\ndescription: fine\n---\nbody\n")
    _write(user_agents / "notes.md", "---\ntitle: a document\n---\nnot an agent\n")
    _write(user_agents / "nameless.md", "# just text\n")
    _write(user_agents / "half.md", "---\nname: half\n---\nbody\n")
    _write(project_agents / "dir" / "AGENT.md", "---\nname: 3\ndescription: d\n---\nbody\n")
    _write(project_agents / "phase.md", "---\nname: bad\ndescription: d\nphase: BLUE\n---\nbody\n")
    _write(project_agents / "README.md", "---\nname: readme\n---\n")
    failed = get_failed_agent_files(project, home)
    assert [(Path(p).name, e) for p, e in failed] == [
        ("half.md", 'Missing required "description" field in frontmatter'),
        ("AGENT.md", 'Missing required "name" field in frontmatter'),
        ("phase.md", failed[2][1]),
    ]
    assert "phase" in failed[2][1].lower()
    assert get_failed_agent_files(tmp_path / "none", tmp_path / "none") == []


def test_get_parse_error():
    assert get_parse_error({"name": ""}) == 'Missing required "name" field in frontmatter'
    assert get_parse_error({"name": "a", "description": 5}) == 'Missing required "description" field in frontmatter'
    assert get_parse_error({"name": "a", "description": "d"}) == "Unknown parsing error"


# ── McpParsingWarnings ───────────────────────────────────────────────────────

def test_an_mcp_scope_section():
    errors = [McpValidationError(path="mcpServers.a.command", message="Required", scope="project", severity="fatal",
                                 server_name="a"),
              McpValidationError(path="", message="Bad JSON", scope="project", severity="fatal"),
              McpValidationError(path="x", message="Odd", scope="project", severity="warning", server_name="b")]
    assert mcp_config_error_section("project", "/p", None, errors) == [
        "", "[Failed to parse] Project config (shared via .mcp.json)", "Location: /p/.mcp.json",
        " └ [Error] [a] mcpServers.a.command: Required", " └ [Error] Bad JSON", " └ [Warning] [b] x: Odd"]
    assert mcp_config_error_section("user", "/p", None, errors[2:])[1] == (
        "[Contains warnings] User config (available in all your projects)")
    assert mcp_config_error_section("user", "/p", None, []) == []
    assert get_scope_label("enterprise") == "Enterprise config (managed by your organization)"
    assert get_scope_label("other") == "other"


def test_mcp_parsing_warnings_reads_every_scope(tmp_path):
    loc = _loc(tmp_path)
    assert mcp_parsing_warnings(loc) == []
    _write(loc.project_root / ".mcp.json", "{broken")
    lines = mcp_parsing_warnings(loc)
    assert lines[:3] == ["", "MCP Config Diagnostics", ""]
    assert lines[3] == "[Failed to parse] Project config (shared via .mcp.json)"
    assert " └ [Error] MCP config is not a valid JSON" in lines and lines[-1] == ""


# ── the screen ───────────────────────────────────────────────────────────────

DIAGNOSTIC = DiagnosticInfo("development", "unknown", "/opt/tddagents", "/opt/tddagents/app/main.py",
                            RipgrepStatus(True, "builtin", None))


def test_the_diagnostics_section():
    assert diagnostics_section(DIAGNOSTIC, []) == [
        "Diagnostics", "└ Currently running: development (unknown)", "└ Path: /opt/tddagents",
        "└ Invoked: /opt/tddagents/app/main.py", "└ Search: OK (vendor)"]
    broken = DiagnosticInfo("development", "unknown", "/p", "/i", RipgrepStatus(False, "system", "/usr/bin/rg"),
                            (ConfigurationWarning("issue", "fix"),))
    errors = [ValidationError(file="/s.json", path="disableAllHooks", message="Expected boolean, but received string")]
    lines = diagnostics_section(broken, errors)
    assert lines[4:] == ["└ Search: Not working (/usr/bin/rg)", "", "Warning: issue", "Fix: fix", "",
                         "Invalid Settings", "/s.json", " └ disableAllHooks: Expected boolean, but received string",
                         ""]
    assert diagnostics_section(DiagnosticInfo("development", "unknown", "/p", "/i",
                                              RipgrepStatus(True, "system", None)), [])[4] == "└ Search: OK (system)"


def test_the_error_sections():
    assert agent_parse_errors_section([]) == [] and plugin_errors_section([]) == []
    assert agent_parse_errors_section([("/a.md", "bad")]) == [
        "Agent Parse Errors", "└ Failed to parse 1 agent file(s):", "  └ /a.md: bad"]
    errors: list[Any] = [{"type": "generic-error", "source": "lint@team", "plugin": "lint", "error": "boom"},
                         {"type": "generic-error", "source": "", "error": "no source"}]
    assert plugin_errors_section(errors) == [
        "Plugin Errors", "└ 2 plugin error(s) detected:", "  └ lint@team [lint]: boom", "  └ unknown: no source"]


def test_the_warning_sections():
    warning = ContextWarning("unreachable_rules", "warning", "1 unreachable permission rule detected",
                             ("Bash(ls): why", "  Fix: how"), 1, 0)
    assert unreachable_rules_section(None) == []
    assert unreachable_rules_section(warning) == [
        "Unreachable Permission Rules", "└ ⚠ 1 unreachable permission rule detected", "  └ Bash(ls): why",
        "  └   Fix: how"]
    assert context_usage_section(ContextWarnings(unreachable_rules_warning=warning)) == []
    md = ContextWarning("claudemd_files", "warning", "Large file", ("/a: 1 chars",), 1, 1)
    mcp = ContextWarning("mcp_tools", "warning", "Large MCP", ("s: 1 tools (~9 tokens)",), 9, 1)
    agents = ContextWarning("agent_descriptions", "warning", "Large agents", ("a: ~9 tokens",), 9, 1)
    assert context_usage_section(ContextWarnings(claude_md_warning=md, mcp_warning=mcp)) == [
        "Context Usage Warnings", "└ ⚠ Large file", "  └ Files:", "    └ /a: 1 chars",
        "└ ⚠ Large MCP", "  └ MCP servers:", "    └ s: 1 tools (~9 tokens)"]
    assert context_usage_section(ContextWarnings(agent_warning=agents))[1:] == [
        "└ ⚠ Large agents", "  └ Top contributors:", "    └ a: ~9 tokens"]


def test_the_whole_screen_in_upstreams_order(tmp_path):
    loc = _loc(tmp_path)
    _write(loc.user_config_dir / "settings.json", {"disableAllHooks": "no"})
    _write(loc.project_root / ".mcp.json", "{broken")

    class Ws:
        kind = "local"

    context = DoctorContext(
        locations=loc, workspace=Ws(), active_agents=[_agent("a", "d")],
        permission_context=ToolPermissionContext(always_allow_rules=(_rule(ALLOW, "Bash", "ls"),),
                                                 always_deny_rules=(_rule(DENY, "Bash"),)),
        plugin_errors=[{"type": "generic-error", "source": "p@m", "error": "boom"}],
        memory_files=lambda: [_memory("/big.md", MAX_MEMORY_CHARACTER_COUNT + 1)],
        failed_agent_files=lambda: [("/half.md", "missing")], count_tokens=_counter(0),
        environ={"BASH_MAX_OUTPUT_LENGTH": "x"})
    lines = run(doctor_lines(context))
    headings = [line for line in lines if line in (
        "Diagnostics", "Invalid Settings", "MCP Config Diagnostics", "Environment Variables", "Agent Parse Errors",
        "Plugin Errors", "Unreachable Permission Rules", "Context Usage Warnings")]
    assert headings == ["Diagnostics", "Invalid Settings", "MCP Config Diagnostics", "Environment Variables",
                        "Agent Parse Errors", "Plugin Errors", "Unreachable Permission Rules",
                        "Context Usage Warnings"]
    invalid = lines.index("Invalid Settings")
    assert lines[invalid + 1] == str(loc.user_config_dir / "settings.json")
    # The MCP config's error is in its own section, not among the invalid settings.
    assert not any("MCP config is not a valid JSON" in line for line in lines[:lines.index("MCP Config Diagnostics")])
    bare = run(doctor_lines(DoctorContext(locations=None, environ={})))
    assert bare[0] == "Diagnostics" and "Invalid Settings" not in bare and "MCP Config Diagnostics" not in bare


def test_the_screen_is_dismissed_by_enter_or_esc():
    async def lines(context: DoctorContext) -> list[str]:
        return ["Diagnostics", "└ x"]

    for answer in ("", "anything", ESC):
        io = FakeDialog(answer)
        assert run(doctor(io, DoctorContext(locations=None), lines)) == DISMISSED == "TDDAgents diagnostics dismissed"
        assert io.out[0] == "Checking installation status…"
        assert io.asked == [f"Diagnostics\n└ x\n\n{PRESS_ENTER_TO_CONTINUE}"]
    assert PRESS_ENTER_TO_CONTINUE == "Press Enter to continue…"


def test_the_command_calls_the_screen():
    io = FakeDialog("")
    assert run(call(io, DoctorContext(locations=None, environ={}))) == DISMISSED
    assert io.asked[0].startswith("Diagnostics\n└ Currently running: development (unknown)\n")
