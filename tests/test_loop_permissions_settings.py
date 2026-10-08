"""
Settings-file permission rules (`app/loop/permissions/settings.py`) and shell rule matching
(`app/loop/permissions/shell_rules.py`): ports of `permissionRuleParser.ts`,
`permissionsLoader.ts` and `shellRuleMatching.ts`.
"""

import json
import logging
from pathlib import Path

import pytest

from app.loop.permissions import settings as st
from app.loop.permissions.settings import (
    LEGACY_TOOL_NAME_ALIASES,
    SETTINGS_SOURCES,
    SUPPORTED_RULE_BEHAVIORS,
    build_tool_permission_context,
    escape_rule_content,
    load_permission_rules,
    normalize_legacy_tool_name,
    permission_rule_value_from_string,
    settings_json_to_rules,
    unescape_rule_content,
)
from app.loop.permissions.shell_rules import (
    ShellPermissionRule,
    has_wildcards,
    match_wildcard_pattern,
    parse_permission_rule,
    permission_rule_extract_prefix,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
)

ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK
USER, PROJECT, LOCAL = (PermissionRuleSource.USER_SETTINGS, PermissionRuleSource.PROJECT_SETTINGS,
                        PermissionRuleSource.LOCAL_SETTINGS)


# ── rule strings ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rule, expected", [
    ("Bash", ("Bash", None)),
    ("Bash(npm install)", ("Bash", "npm install")),
    ("Bash(npm:*)", ("Bash", "npm:*")),
    ("Bash()", ("Bash", None)),
    ("Bash(*)", ("Bash", None)),
    ("Bash(python -c \"print\\(1\\)\")", ("Bash", 'python -c "print(1)"')),
    ("Bash(a\\\\b)", ("Bash", "a\\b")),
    ("Bash(unclosed", ("Bash(unclosed", None)),
    ("Bash)x(", ("Bash)x(", None)),
    ("Bash(x)tail", ("Bash(x)tail", None)),
    ("(x)", ("(x)", None)),
    ("Bash\\(x\\)", ("Bash\\(x\\)", None)),
    ("Task", ("Agent", None)),
    ("Task(explore)", ("Agent", "explore")),
    ("KillShell", ("TaskStop", None)),
    ("AgentOutputTool", ("TaskOutput", None)),
    ("BashOutputTool", ("TaskOutput", None)),
    ("Bash(a(b)c)", ("Bash", "a(b)c")),
])
def test_permission_rule_value_from_string(rule, expected):
    assert permission_rule_value_from_string(rule) == expected


def test_escape_and_unescape_round_trip():
    original = 'python -c "print(1)" \\ end'
    escaped = escape_rule_content(original)
    assert escaped == 'python -c "print\\(1\\)" \\\\ end'
    assert unescape_rule_content(escaped) == original
    assert permission_rule_value_from_string(f"Bash({escaped})") == ("Bash", original)


def test_legacy_aliases():
    assert LEGACY_TOOL_NAME_ALIASES == {
        "Task": "Agent", "KillShell": "TaskStop", "AgentOutputTool": "TaskOutput", "BashOutputTool": "TaskOutput"}
    assert normalize_legacy_tool_name("Bash") == "Bash"


def test_constants_order():
    assert SUPPORTED_RULE_BEHAVIORS == (ALLOW, DENY, ASK)
    assert SETTINGS_SOURCES == (USER, PROJECT, LOCAL)


# ── settings JSON → rules ────────────────────────────────────────────────────

def test_settings_json_to_rules_order_and_shape():
    data = {"permissions": {"ask": ["Bash(rm:*)"], "deny": ["WebFetch"], "allow": ["Bash(ls)", "Read"]}}
    assert settings_json_to_rules(data, PROJECT) == [
        PermissionRule("Bash", ALLOW, "ls", PROJECT),
        PermissionRule("Read", ALLOW, None, PROJECT),
        PermissionRule("WebFetch", DENY, None, PROJECT),
        PermissionRule("Bash", ASK, "rm:*", PROJECT),
    ]


@pytest.mark.parametrize("data", [None, [], "x", {}, {"permissions": []}, {"permissions": "x"}, {"other": 1}])
def test_settings_json_to_rules_ignores_non_objects(data):
    assert settings_json_to_rules(data, USER) == []


def test_settings_json_to_rules_skips_malformed_entries(caplog):
    data = {"permissions": {"allow": "Bash", "deny": [1, "Bash(x)", None], "ask": None}}
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        rules = settings_json_to_rules(data, LOCAL, "f.json")
    assert rules == [PermissionRule("Bash", DENY, "x", LOCAL)]
    messages = [r.getMessage() for r in caplog.records]
    assert "Ignoring non-list permissions.allow in f.json." in messages
    assert messages.count("Ignoring non-string permissions.deny entry in f.json.") == 2


def write_settings(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_load_permission_rules_reads_all_three_scopes_in_order(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    write_settings(home / ".tddagents/settings.json", {"permissions": {"allow": ["Bash(u)"]}})
    write_settings(project / ".tddagents/settings.json", {"permissions": {"allow": ["Bash(p)"]}})
    write_settings(project / ".tddagents/settings.local.json", {"permissions": {"deny": ["Bash(l)"]}})
    assert load_permission_rules(project, home) == [
        PermissionRule("Bash", ALLOW, "u", USER),
        PermissionRule("Bash", ALLOW, "p", PROJECT),
        PermissionRule("Bash", DENY, "l", LOCAL),
    ]
    assert load_permission_rules(str(project), home)[1].source == PROJECT


def test_load_permission_rules_missing_and_malformed_files(tmp_path, caplog):
    home, project = tmp_path / "home", tmp_path / "proj"
    assert load_permission_rules(project, home) == []
    write_settings(project / ".tddagents/settings.json", "{not json")
    (project / ".tddagents/settings.local.json").mkdir()  # a directory, not a file
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        assert load_permission_rules(project, home) == []
    assert any(r.getMessage().startswith("Ignoring malformed settings at ") for r in caplog.records)


def test_non_utf8_settings_are_logged_and_skipped(tmp_path, caplog):
    project = tmp_path / "proj"
    bad = project / ".tddagents/settings.json"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe")
    write_settings(project / ".tddagents/settings.local.json", {"permissions": {"allow": ["Bash(ok)"]}})
    assert st._read_settings(tmp_path / "missing.json") is None
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        rules = load_permission_rules(project, tmp_path / "home")
    assert [r.rule_content for r in rules] == ["ok"]
    assert any(r.getMessage().startswith(f"Ignoring malformed settings at {bad}") for r in caplog.records)


def test_build_tool_permission_context(tmp_path):
    project = tmp_path / "proj"
    write_settings(project / ".tddagents/settings.json",
                   {"permissions": {"allow": ["Bash(a)"], "deny": ["Bash(d)"], "ask": ["Bash(q)"]}})
    ctx = build_tool_permission_context(project, tmp_path / "home")
    assert ctx.mode == PermissionMode.DEFAULT
    assert ctx.should_avoid_permission_prompts is True
    assert [r.rule_content for r in ctx.always_allow_rules] == ["a"]
    assert [r.rule_content for r in ctx.always_deny_rules] == ["d"]
    assert [r.rule_content for r in ctx.always_ask_rules] == ["q"]
    other = build_tool_permission_context(project, tmp_path / "home", mode=PermissionMode.ACCEPT_EDITS,
                                          should_avoid_permission_prompts=False)
    assert other.mode == PermissionMode.ACCEPT_EDITS
    assert other.should_avoid_permission_prompts is False


def test_shipped_project_settings_parse():
    """The checked-in `.tddagents/settings.json` loads and only allows."""
    repo = Path(__file__).parent.parent
    rules = load_permission_rules(repo, repo / "no-such-home")
    assert rules and all(r.rule_behavior == ALLOW and r.tool_name == "Bash" for r in rules)
    assert "pytest:*" in {r.rule_content for r in rules}


# ── shell rule matching ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rule, prefix", [
    ("npm:*", "npm"), ("git commit:*", "git commit"), ("npm", None), (":*", None), ("a:*b", None),
    ("x\n:*", "x\n"),
])
def test_permission_rule_extract_prefix(rule, prefix):
    assert permission_rule_extract_prefix(rule) == prefix


@pytest.mark.parametrize("pattern, expected", [
    ("git *", True), ("*", True), ("npm:*", False), ("a\\*b", False), ("a\\\\*b", True),
    ("plain", False), ("a\\\\\\*", False),
])
def test_has_wildcards(pattern, expected):
    assert has_wildcards(pattern) is expected


@pytest.mark.parametrize("pattern, command, expected", [
    ("git *", "git status", True),
    ("git *", "git", True),
    ("git *", "gitx", False),
    ("git * --dry-run", "git push --dry-run", True),
    ("git * --dry-run", "git push", False),
    ("* foo *", "a foo b", True),
    ("echo \\*", "echo *", True),
    ("echo \\*", "echo x", False),
    ("a\\\\b", "a\\b", True),
    ("a.b", "axb", False),
    ("a.b", "a.b", True),
    ("(x)|[y]", "(x)|[y]", True),
    ("ls *", "ls -la\nrm -rf /", True),
    ("  ls *  ", "ls a", True),
    ("Git *", "git x", False),
    ("echo $HOME", "echo $HOME", True),
    ("it's", "it's", True),
    ('say "hi"', 'say "hi"', True),
    ("a?b{c}^", "a?b{c}^", True),
    ("x\\", "x\\", True),
])
def test_match_wildcard_pattern(pattern, command, expected):
    assert match_wildcard_pattern(pattern, command) is expected


def test_match_wildcard_pattern_case_insensitive_and_multi_star():
    assert match_wildcard_pattern("Git *", "git x", case_insensitive=True) is True
    assert match_wildcard_pattern("a * b *", "a x b") is False  # two stars: no optional tail
    assert match_wildcard_pattern("a * b *", "a x b y") is True


@pytest.mark.parametrize("rule, expected", [
    ("npm:*", ShellPermissionRule("prefix", "npm")),
    ("git * --x", ShellPermissionRule("wildcard", "git * --x")),
    ("ls -la", ShellPermissionRule("exact", "ls -la")),
    ("echo \\*", ShellPermissionRule("exact", "echo \\*")),
])
def test_parse_permission_rule(rule, expected):
    assert parse_permission_rule(rule) == expected


# ── mutation-driven pins ─────────────────────────────────────────────────────

@pytest.mark.parametrize("s, i, expected", [
    ("\\(", 1, True),            # one backslash at index 0
    ("a\\\\(", 3, False),        # two: escaped backslash, live paren
    ("a\\\\\\(", 4, True),       # three
    ("a\\\\\\\\(", 5, False),    # four
    ("(", 0, False),
])
def test_escaped(s, i, expected):
    assert st._escaped(s, i) is expected


def test_unescaped_paren_at_start_and_missing_open():
    assert permission_rule_value_from_string("\\(Bash(x)") == ("\\(Bash", "x")
    assert permission_rule_value_from_string("Ba)") == ("Ba)", None)
    assert st._find_first_unescaped("abc", "(") == -1
    assert st._find_last_unescaped("abc", ")") == -1


def test_build_context_uses_the_given_home(tmp_path):
    home = tmp_path / "home"
    write_settings(home / ".tddagents/settings.json", {"permissions": {"allow": ["Bash(from-home)"]}})
    ctx = build_tool_permission_context(tmp_path / "proj", home)
    assert [r.rule_content for r in ctx.always_allow_rules] == ["from-home"]


def test_warnings_name_the_file_and_the_error(tmp_path, caplog):
    project = tmp_path / "proj"
    bad = project / ".tddagents/settings.json"
    write_settings(bad, "{nope")
    local = project / ".tddagents/settings.local.json"
    write_settings(local, {"permissions": {"allow": "Bash"}})
    with caplog.at_level(logging.WARNING, logger="TDDOrchestrator.Permissions"):
        load_permission_rules(project, tmp_path / "home")
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith(f"Ignoring malformed settings at {bad}: Expecting") for m in messages)
    assert f"Ignoring non-list permissions.allow in {local}." in messages


def test_backslash_at_index_zero_escapes_the_star():
    assert has_wildcards("\\*x") is False


def test_escaped_star_mid_pattern_keeps_the_rest():
    assert match_wildcard_pattern("a\\*b", "a*b") is True
    assert match_wildcard_pattern("a\\*b *", "a*b c") is True
