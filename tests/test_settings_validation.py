"""
Settings validation as upstream's (`reference/claude-code/src/utils/settings/settings.ts` ->
`parseSettingsFileUncached`, `validation.ts`, `permissionValidation.ts`, `validationTips.ts`,
`components/InvalidSettingsDialog.tsx`, `ValidationErrorsList.tsx`): a file failing the settings schema
is ignored with its errors, invalid permission rules are dropped with a warning, unknown keys follow
Zod (passthrough at the top level and in `permissions`, stripped elsewhere).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.bootstrap.state import set_flag_settings_inline
from app.components.invalid_settings_dialog import invalid_settings_dialog
from app.components.trust_dialog.trust_dialog import TrustDialogExit
from app.components.validation_errors_list import build_nested_tree, render_validation_errors
from app.interactive_helpers import launch_invalid_settings_dialog
from app.types.permissions import PermissionRuleSource
from app.utils.hooks.hooks_config_snapshot import capture_hooks_config_snapshot
from app.hooks.notifs.use_settings_errors import settings_errors_notification_text, use_settings_errors
from app.utils.permissions.filesystem import get_settings_paths, is_tddagents_settings_path
from app.utils.permissions.permission_update import update_settings_for_source
from app.utils.settings.permission_validation import permission_rule_issue_message, validate_permission_rule
from app.utils.settings.schema_output import generate_settings_json_schema
from app.utils.settings.schema_validator import safe_parse_settings, strip_unknown_keys
from app.utils.settings.settings import (
    SettingsLocations,
    get_settings_with_errors,
    load_all_permission_rules_from_disk,
    load_settings,
    parse_settings_document,
    parse_settings_file_with_errors,
)
from app.utils.settings.types import GATED_OFF_SETTINGS_KEYS, SETTINGS_SCHEMA_URL
from app.utils.settings.validate_edit_tool import (
    SETTINGS_VALIDATION_ERROR_CODE,
    validate_input_for_settings_file_edit,
)
from app.utils.settings.validation import (
    UNDEFINED,
    SchemaIssue,
    SettingsFileContentValidation,
    ValidationError,
    extract_received_from_message,
    format_zod_error,
    get_received_type,
    validate_settings_file_content,
)
from app.utils.settings.validation_tips import TipContext, get_validation_tip
from app.utils.treeify import treeify
from tests.dialog_fakes import ESC, FakeDialog

REPO = Path(__file__).resolve().parent.parent


def _loc(tmp_path: Path) -> SettingsLocations:
    return SettingsLocations(project_root=tmp_path / "project", home=tmp_path / "home",
                             managed_dir=tmp_path / "managed")


def _write(path: Path, content: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def _parse(document: Any) -> tuple[Any, list[tuple[str, str]]]:
    parsed, errors = parse_settings_document(document, "f.json")
    return parsed, [(e.path, e.message) for e in errors]


# ── the shipped settings and valid files ─────────────────────────────────────

def test_the_shipped_settings_are_valid() -> None:
    parsed, errors = parse_settings_file_with_errors(REPO / ".tddagents" / "settings.json")
    assert errors == [] and parsed == {"permissions": {"defaultMode": "acceptEdits"}}


def test_a_valid_file_is_the_parsed_document_unknown_keys_as_zod() -> None:
    document: dict[str, Any] = {
        "$schema": SETTINGS_SCHEMA_URL, "model": "sonnet", "env": {"A": 1},  # keys this build does not read
        "permissions": {"allow": ["Bash(ls)"], "defaultMode": "plan", "futureKey": True},
        "hooks": {"Stop": [{"matcher": "", "note": 1, "hooks": [{"type": "command", "command": "x", "junk": 2}]}]},
        "allowedMcpServers": [{"serverName": "ok", "comment": "x"}],
        "enabledPlugins": {"p@m": True},
    }
    parsed, errors = _parse(document)
    assert errors == []
    assert parsed == {
        "$schema": SETTINGS_SCHEMA_URL, "model": "sonnet", "env": {"A": "1"},  # `z.coerce.string()`
        "permissions": {"allow": ["Bash(ls)"], "defaultMode": "plan", "futureKey": True},
        "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "x"}]}]},
        "allowedMcpServers": [{"serverName": "ok"}],
        "enabledPlugins": {"p@m": True},
    }
    assert document["hooks"]["Stop"][0]["note"] == 1  # the caller's document is not touched


def test_empty_and_missing_files(tmp_path: Path) -> None:
    assert parse_settings_file_with_errors(tmp_path / "missing.json") == (None, [])
    assert parse_settings_file_with_errors(_write(tmp_path / "empty.json", " \n")) == ({}, [])


# ── invalid files ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("document, expected", [
    (None, [("", "Invalid or malformed JSON")]),
    ([1], [("", "Expected object, but received array")]),
    ({"permissions": {"allow": "Bash"}}, [("permissions.allow", "Expected array, but received string")]),
    ({"disableAllHooks": "yes"}, [("disableAllHooks", "Expected boolean, but received string")]),
    ({"permissions": {"defaultMode": "nope"}}, [("permissions.defaultMode", 'Invalid value. Expected one of: '
                                                 '"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", '
                                                 '"auto"')]),
    ({"permissions": {"disableBypassPermissionsMode": "x"}},
     [("permissions.disableBypassPermissionsMode", 'Invalid value. Expected one of: "disable"')]),
    ({"hooks": {"Nope": []}}, [("hooks.Nope", "Invalid key in record")]),
    ({"hooks": {"Stop": [{"hooks": [{"type": "command"}]}]}},
     [("hooks.Stop.0.hooks.0.command", "Expected string, but received undefined")]),
    ({"hooks": {"Stop": [{"hooks": [{"type": "nope", "command": "x"}]}]}},
     [("hooks.Stop.0.hooks.0.type", "Invalid input")]),
    ({"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}, [("hooks.Stop.0.hooks.0.type", "Invalid input")]),
    ({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x", "timeout": -1}]}]}},
     [("hooks.Stop.0.hooks.0.timeout", "Number must be greater than or equal to 0")]),
    ({"hooks": {"Stop": [{"hooks": [{"type": "http", "url": "not a url"}]}]}},
     [("hooks.Stop.0.hooks.0.url", "Invalid URL")]),
    ({"allowedMcpServers": [{"serverName": "a", "serverUrl": "b"}, {"serverName": "bad name"}]}, [
        ("allowedMcpServers.1.serverName", "Server name can only contain letters, numbers, hyphens, and underscores"),
        ("allowedMcpServers.0", 'Entry must have exactly one of "serverName", "serverCommand", or "serverUrl"')]),
    ({"deniedMcpServers": [{"serverCommand": []}]},
     [("deniedMcpServers.0.serverCommand", "Number must be greater than or equal to 1")]),
    ({"enabledPlugins": {"p@m": None}}, [("enabledPlugins.p@m", "Invalid input")]),
    ({"enabledMcpjsonServers": "a"}, [("enabledMcpjsonServers", "Expected array, but received string")]),
])
def test_an_invalid_document_is_ignored_with_upstreams_messages(document: Any, expected: list[tuple[str, str]]) -> None:
    parsed, errors = _parse(document)
    assert parsed is None and errors == expected


def test_strict_plugin_only_customization_never_fails_the_file() -> None:
    assert _parse({"strictPluginOnlyCustomization": ["skills", "commands", 3]}) == (
        {"strictPluginOnlyCustomization": ["skills"]}, [])
    assert _parse({"strictPluginOnlyCustomization": "skills"}) == ({}, [])
    assert _parse({"strictPluginOnlyCustomization": True}) == ({"strictPluginOnlyCustomization": True}, [])


def test_invalid_permission_rules_are_dropped_with_a_warning_and_the_file_kept() -> None:
    parsed, errors = parse_settings_document(
        {"permissions": {"allow": ["Bash()", 3, "Bash(ls)"], "deny": ["read"], "ask": "x"}}, "f.json")
    assert parsed is None  # `ask` is not an array: the schema still fails, after the warnings
    assert [(e.path, e.message) for e in errors][:3] == [
        ("permissions.allow", 'Invalid permission rule "Bash()" was skipped: Empty parentheses. Either specify a '
                              'pattern or use just "Bash" without parentheses'),
        ("permissions.allow", "Non-string value in allow array was removed"),
        ("permissions.deny", 'Invalid permission rule "read" was skipped: Tool names must start with uppercase. '
                             'Use "Read"'),
    ]
    assert [e.invalid_value for e in errors[:3]] == ["Bash()", 3, "read"]
    parsed, errors = parse_settings_document({"permissions": {"allow": ["Bash()", "Bash(ls)"]}}, "f.json")
    assert parsed == {"permissions": {"allow": ["Bash(ls)"]}} and len(errors) == 1


def test_an_invalid_file_is_ignored_whole_and_reported(tmp_path: Path, settings_debug_log: list[str]) -> None:
    loc = _loc(tmp_path)
    project = _write(loc.project_root / ".tddagents" / "settings.json", {
        "permissions": {"deny": ["Bash(rm:*)"], "defaultMode": "plan"},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
        "disableAllHooks": "no",
    })
    _write(loc.user_config_dir / "settings.json", {"permissions": {"allow": ["Bash(ls)"]}})
    merged, errors = get_settings_with_errors(loc)
    assert merged == {"permissions": {"allow": ["Bash(ls)"]}}
    assert [(e.file, e.path, e.message) for e in errors] == [
        (str(project), "disableAllHooks", "Expected boolean, but received string")]
    assert errors[0].suggestion == 'Use true or false without quotes. Example: "includeCoAuthoredBy": true'
    assert settings_debug_log == [
        f"Invalid settings at {project}, file ignored:\ndisableAllHooks: Expected boolean, but received string"]
    assert [r.tool_name for r in load_all_permission_rules_from_disk(loc)] == ["Bash"]
    assert capture_hooks_config_snapshot(loc).hooks == {}


def test_errors_are_reported_once_and_policy_files_count(tmp_path: Path) -> None:
    loc = SettingsLocations(project_root=tmp_path, home=tmp_path, managed_dir=tmp_path / "managed")
    _write(loc.user_config_dir / "settings.json", {"disableAllHooks": 1})  # user and project: one file
    _write(loc.managed_dir / "managed-settings.json", {"allowManagedHooksOnly": "x"})
    _write(loc.managed_dir / "managed-settings.d" / "10.json", "{")
    _, errors = get_settings_with_errors(loc)
    assert [(Path(e.file or "").name, e.path) for e in errors] == [
        ("settings.json", "disableAllHooks"), ("managed-settings.json", "allowManagedHooksOnly"), ("10.json", "")]


def test_invalid_inline_flag_settings_are_ignored(tmp_path: Path) -> None:
    loc = _loc(tmp_path)
    try:
        set_flag_settings_inline({"disableAllHooks": "x", "permissions": {"allow": ["Bash(ls)"]}})
        assert load_settings(loc) == {}
        set_flag_settings_inline({"disableAllHooks": True})
        assert load_settings(loc) == {"disableAllHooks": True}
    finally:
        set_flag_settings_inline(None)


def test_writing_into_an_invalid_file_merges_into_it_as_upstream(tmp_path: Path) -> None:
    loc = _loc(tmp_path)
    path = _write(loc.project_root / ".tddagents" / "settings.json", {"disableAllHooks": "no", "x": 1})
    assert update_settings_for_source(PermissionRuleSource.PROJECT_SETTINGS, loc, {"permissions": {"ask": ["Bash"]}}) \
        is None
    assert json.loads(path.read_text()) == {"disableAllHooks": "no", "x": 1, "permissions": {"ask": ["Bash"]}}
    _write(path, "{nope")
    assert update_settings_for_source(PermissionRuleSource.PROJECT_SETTINGS, loc, {"x": 2}) == (
        f"Invalid JSON syntax in settings file at {path}")
    _write(path, "[1]")  # valid JSON, not an object: written over, as upstream
    assert update_settings_for_source(PermissionRuleSource.PROJECT_SETTINGS, loc, {"x": 2}) is None
    assert json.loads(path.read_text()) == {"x": 2}


# ── the pieces ───────────────────────────────────────────────────────────────

def test_format_zod_error() -> None:
    issues = [
        SchemaIssue(code="invalid_type", path=("a",), expected="boolean", message="Invalid input: expected boolean, "
                                                                                  "received string", input="x"),
        SchemaIssue(code="invalid_type", path=("b",), expected="string", message="no hint", input=UNDEFINED),
        SchemaIssue(code="unrecognized_keys", path=(), keys=("x", "y"), message="Unrecognized keys"),
        SchemaIssue(code="unrecognized_keys", path=(), keys=("x",), message="Unrecognized key"),
        SchemaIssue(code="too_small", path=("n",), minimum=1, message="Too small"),
        SchemaIssue(code="too_big", path=("s",), message="Too big: expected string to have <=40 characters"),
        SchemaIssue(code="custom", path=("c", 0), message="Bad", has_params=True, params_received="v"),
        SchemaIssue(code="invalid_value", path=("e",), values=(True, 2.0), message="Invalid option"),
    ]
    errors = format_zod_error(issues, "f.json")
    assert [(e.path, e.message, e.expected, e.invalid_value) for e in errors] == [
        ("a", "Expected boolean, but received string", None, "string"),
        ("b", "Expected string, but received undefined", None, "undefined"),
        ("", "Unrecognized fields: x, y", None, None),
        ("", "Unrecognized field: x", None, None),
        ("n", "Number must be greater than or equal to 1", "1", None),
        ("s", "Too big: expected string to have <=40 characters", None, None),
        ("c.0", "Bad", None, "v"),
        ("e", 'Invalid value. Expected one of: "true", "2"', '"true", "2"', None),
    ]
    assert all(e.file == "f.json" for e in errors)
    assert errors[2].suggestion == "Check for typos or refer to the documentation for valid fields"
    assert errors[7].suggestion == 'Valid values: "true", "2"'
    values: tuple[Any, ...] = (None, UNDEFINED, [], True, 1, 1.5, "s", {})
    assert [get_received_type(v) for v in values] == [
        "null", "undefined", "array", "boolean", "number", "number", "string", "object"]
    assert extract_received_from_message("x, received null") == "null"
    assert extract_received_from_message("nothing") is None


def test_validation_tips() -> None:
    hooks = get_validation_tip(TipContext(path="hooks.Stop", code="invalid_type", expected="array"))
    assert hooks is not None and '"Edit|WriteFile"' in (hooks.suggestion or "")
    directories = get_validation_tip(TipContext(path="permissions.additionalDirectories", code="invalid_type"))
    assert directories is not None and directories.suggestion == (
        'Must be an array of directory paths. Example: ["~/projects", "/tmp/workspace"].')
    assert get_validation_tip(TipContext(path="x", code="custom")) is None
    assert get_validation_tip(TipContext(path="x", code="invalid_value", enum_values=[])) is not None
    # Upstream's malformed-JSON tip compares the received type with `null` and never matches.
    assert get_validation_tip(TipContext(path="", code="invalid_type", expected="object", received="null")) is None


@pytest.mark.parametrize("rule, message", [
    ("", "Permission rule cannot be empty"),
    ("Bash(ls", "Mismatched parentheses. Ensure all opening parentheses have matching closing parentheses"),
    ("()", "Empty parentheses with no tool name. Specify a tool name before the parentheses"),
    ("Bash()", 'Empty parentheses. Either specify a pattern or use just "Bash" without parentheses. Examples: Bash, '
               "Bash(some-pattern)"),
    ("mcp__srv__tool(x)", 'MCP rules do not support patterns in parentheses. Use "mcp__srv__tool" without '
                          'parentheses, or use "mcp__srv__*" for all tools. Examples: mcp__srv, mcp__srv__*, '
                          "mcp__srv__tool"),
    ("mcp__srv(*)", 'MCP rules do not support patterns in parentheses. Use "mcp__srv" without parentheses, or use '
                    '"mcp__srv__*" for all tools. Examples: mcp__srv, mcp__srv__*'),
    ("bash", 'Tool names must start with uppercase. Use "Bash"'),
    ("WebSearch(a*)", "WebSearch does not support wildcards. Use exact search terms without * or ?. Examples: "
                      "WebSearch(claude ai), WebSearch(typescript tutorial)"),
    ("WebFetch(https://x)", 'WebFetch permissions use domain format, not URLs. Use "domain:hostname" format. '
                            "Examples: WebFetch(domain:example.com), WebFetch(domain:github.com)"),
    ("WebFetch(x.com)", 'WebFetch permissions must use "domain:" prefix. Use "domain:hostname" format. Examples: '
                        "WebFetch(domain:example.com), WebFetch(domain:*.google.com)"),
    ("Bash(npm:* x)", "The :* pattern must be at the end. Move :* to the end for prefix matching, or use * for "
                      "wildcard matching. Examples: Bash(npm run:*) - prefix matching (legacy), Bash(npm run *) - "
                      "wildcard matching"),
    ("Bash(:*)", "Prefix cannot be empty before :*. Specify a command prefix before :*. Examples: Bash(npm:*), "
                 "Bash(git:*)"),
    ("Edit(src:*)", 'The ":*" syntax is only for Bash prefix rules. Use glob patterns like "*" or "**" for file '
                    "matching. Examples: Edit(*.ts) - matches .ts files, Edit(src/**) - matches all files in src, "
                    "Edit(**/*.test.ts) - matches test files"),
    ("ReadFile(a*b)", "Wildcard placement might be incorrect. Wildcards are typically used at path boundaries. "
                      "Examples: ReadFile(*.js) - all .js files, ReadFile(src/*) - all files directly in src, "
                      "ReadFile(src/**) - all files recursively in src"),
])
def test_invalid_permission_rules(rule: str, message: str) -> None:
    result = validate_permission_rule(rule)
    assert not result.valid and permission_rule_issue_message(result) == message


@pytest.mark.parametrize("rule", [
    "Bash", "Bash(npm run:*)", "Bash(npm *)", "mcp__srv", "mcp__srv__*", "Edit(src/**)", "ReadFile(*.py)",
    "WebFetch(domain:x.com)", "WebSearch(claude)", "Bash(\\(\\))", "Glob(a/**/b)",
])
def test_valid_permission_rules(rule: str) -> None:
    assert validate_permission_rule(rule).valid


def test_strip_unknown_keys() -> None:
    record = {"type": "object", "propertyNames": {"type": "string"},
              "additionalProperties": {"type": "object", "properties": {"a": {"type": "number"}}}}
    assert strip_unknown_keys(record, {"k": {"a": 1, "b": 2}}) == {"k": {"a": 1}}
    union = {"anyOf": [{"type": "string"}, {"type": "object", "properties": {"x": {"type": "number"}}}]}
    assert strip_unknown_keys(union, {"x": 1, "y": 2}) == {"x": 1}
    assert strip_unknown_keys(union, 5) == 5
    assert safe_parse_settings("x")[0] is None


# ── the dialog ───────────────────────────────────────────────────────────────

def test_the_errors_are_drawn_as_a_tree_by_file() -> None:
    errors = [
        ValidationError(file="b.json", path="permissions.allow", message="first"),
        ValidationError(file="b.json", path="permissions.allow", message="second"),  # replaces the first
        ValidationError(file="b.json", path="hooks.Stop.0", message="bad", invalid_value="x"),
        ValidationError(file="a.json", path="", message="Invalid or malformed JSON", suggestion="Fix it",
                        doc_link="https://x"),
        ValidationError(path="z", message="where?"),
    ]
    assert build_nested_tree(errors[:3]) == {"permissions": {"allow": "second"}, "hooks": {"Stop": {'"x"': "bad"}}}
    assert render_validation_errors(errors) == [
        "(file not specified)", " └ z: where?",
        "a.json", " └ Invalid or malformed JSON", "", "Fix it", "Learn more: https://x", "",
        "b.json", " ├ hooks", " │ └ Stop", ' │   └ "x": bad', " └ permissions", "   └ allow: second",
    ]
    assert treeify({}) == "(empty)"
    assert treeify({"10": "b", "2": "a", "k": {"x": [1, 2]}}) == "├ 2: a\n├ 10: b\n└ k\n  └ x: [Array(2)]"


def test_the_dialog_continues_or_exits() -> None:
    errors = [ValidationError(file="s.json", path="disableAllHooks", message="Expected boolean, but received string")]
    io = FakeDialog("2")
    assert asyncio.run(invalid_settings_dialog(io, errors)) is True
    assert io.asked[0] == "\n".join([
        "Settings Error", "", "s.json", " └ disableAllHooks: Expected boolean, but received string",
        "Files with errors are skipped entirely, not just the invalid settings.", "",
        "  1. Exit and fix manually", "  2. Continue without these settings", "Choose 1-2 · Esc to exit"])
    assert asyncio.run(invalid_settings_dialog(FakeDialog("1"), errors)) is False
    assert asyncio.run(invalid_settings_dialog(FakeDialog(ESC), errors)) is False


class _SetupIO:
    def __init__(self, *lines: str | None) -> None:
        self.lines = list(lines)
        self.out: list[str] = []

    async def read_line(self, prompt: str) -> str | None:
        return self.lines.pop(0)

    def write(self, text: str) -> None:
        self.out.append(text)


def test_the_launcher_asks_only_with_errors_and_exits_with_code_1(tmp_path: Path) -> None:
    loc = _loc(tmp_path)
    io = _SetupIO()
    asyncio.run(launch_invalid_settings_dialog(io, asyncio.Event(), loc))
    assert io.out == []
    # An MCP config's error does not ask.
    _write(loc.project_root / ".mcp.json", {"mcpServers": {"bad": {"type": "http"}}})
    asyncio.run(launch_invalid_settings_dialog(io, asyncio.Event(), loc))
    assert io.out == []
    _write(loc.user_config_dir / "settings.json", "{")
    io = _SetupIO("2")
    asyncio.run(launch_invalid_settings_dialog(io, asyncio.Event(), loc))
    assert io.out[0].startswith("Settings Error\n")
    with pytest.raises(TrustDialogExit) as exited:
        asyncio.run(launch_invalid_settings_dialog(_SetupIO("1"), asyncio.Event(), loc))
    assert exited.value.exit_code == 1


# ── unfiltered documents: the inline flag settings, an edit's content ───────────

def test_an_invalid_rule_rejects_an_unfiltered_document(tmp_path: Path) -> None:
    parsed, issues = safe_parse_settings({"permissions": {"allow": ["Bash(ls)", "bash", 3], "deny": "x"}})
    assert parsed is None
    errors = format_zod_error(issues, "f.json")
    assert [(e.path, e.message, e.invalid_value) for e in errors] == [
        ("permissions.allow.2", "Expected string, but received number", "number"),
        ("permissions.deny", "Expected array, but received string", "string"),
        ("permissions.allow.1", 'Tool names must start with uppercase. Use "Bash"', "bash"),
    ]
    loc = _loc(tmp_path)
    try:
        # Upstream's `loadSettingsFromDisk` parses the inline settings without filtering their rules.
        set_flag_settings_inline({"permissions": {"allow": ["Bash(ls)", "bash"]}})
        assert load_settings(loc) == {}
        set_flag_settings_inline({"permissions": {"allow": ["Bash(ls)"]}})
        assert load_settings(loc) == {"permissions": {"allow": ["Bash(ls)"]}}
    finally:
        set_flag_settings_inline(None)


def test_a_discriminated_union_member_must_be_an_object() -> None:
    assert _parse({"hooks": {"Stop": [{"hooks": ["echo"]}]}}) == (
        None, [("hooks.Stop.0.hooks.0", "Expected object, but received string")])


def test_validate_settings_file_content() -> None:
    schema = generate_settings_json_schema()
    assert validate_settings_file_content('{"disableAllHooks": true}') == SettingsFileContentValidation(True)
    invalid_json = validate_settings_file_content("{nope")
    assert not invalid_json.is_valid and invalid_json.error.startswith("Invalid JSON: Expecting property name")
    assert invalid_json.full_schema == schema
    result = validate_settings_file_content(json.dumps({
        "disableAllHooks": "no", "envv": {}, "modle": "x", "permissions": {"allow": ["bash"], "futureKey": 1}}))
    assert result == SettingsFileContentValidation(False, "\n".join([
        "Settings validation failed:",
        "- disableAllHooks: Expected boolean, but received string",
        '- permissions.allow.0: Tool names must start with uppercase. Use "Bash"',
        "- : Unrecognized fields: envv, modle",
    ]), schema)
    one = validate_settings_file_content('{"x": 1}')
    assert one.error == "Settings validation failed:\n- : Unrecognized field: x"
    assert validate_settings_file_content("[]").error == (
        "Settings validation failed:\n- : Expected object, but received array")


def test_strict_parsing_reports_the_unknown_keys_issue() -> None:
    parsed, issues = safe_parse_settings({"a": 1, "hooks": {}}, strict=True)
    assert parsed is None and issues == [SchemaIssue(code="unrecognized_keys", path=(), keys=("a",),
                                                     input={"a": 1, "hooks": {}}, message='Unrecognized key: "a"')]
    _, issues = safe_parse_settings({"a": 1, "b": 2}, strict=True)
    assert issues[0].message == 'Unrecognized keys: "a", "b"'
    assert safe_parse_settings({"a": 1})[0] == {"a": 1}  # not strict: passed through


UNREAD_KEY_VALUES: list[tuple[str, Any]] = [
    ("$schema", SETTINGS_SCHEMA_URL), ("env", {"A": "1"}), ("model", "opus"),
    ("statusLine", {"type": "command", "command": "x", "padding": 1}), ("sandbox", {"enabled": True, "x": 1}),
    ("attribution", {"commit": ""}), ("disableAutoMode", "disable"), ("skipAutoPermissionPrompt", True),
    ("claudeMdExcludes", ["**/x"]), ("cleanupPeriodDays", 0), ("feedbackSurveyRate", 0.5),
    ("sshConfigs", [{"id": "i", "name": "n", "sshHost": "h", "sshPort": 22}]),
]


@pytest.mark.parametrize("key, value", UNREAD_KEY_VALUES)
def test_strict_parsing_accepts_upstreams_keys_this_build_does_not_read(key: str, value: Any) -> None:
    document = {key: value, "disableAllHooks": True}
    assert safe_parse_settings(document, strict=True) == (document, [])
    assert validate_settings_file_content(json.dumps(document)) == SettingsFileContentValidation(True)


@pytest.mark.parametrize("key, value, expected", [
    ("model", 3, [("model", "Expected string, but received number")]),
    ("env", "A=1", [("env", "Expected record, but received string")]),
    ("$schema", "https://example.com/s.json", [("$schema", 'Invalid value. Expected one of: '
                                                '"https://json.schemastore.org/claude-code-settings.json"')]),
    ("statusLine", {"type": "script"}, [("statusLine.type", 'Invalid value. Expected one of: "command"'),
                                        ("statusLine.command", "Expected string, but received undefined")]),
    ("cleanupPeriodDays", -1, [("cleanupPeriodDays", "Number must be greater than or equal to 0")]),
    ("cleanupPeriodDays", 1.5, [("cleanupPeriodDays", "Expected int, but received number")]),
    ("feedbackSurveyRate", 2, [("feedbackSurveyRate", "Too big: expected number to be <=1")]),
    ("sandbox", {"network": {"httpProxyPort": "80"}},
     [("sandbox.network.httpProxyPort", "Expected number, but received string")]),
    ("xaaIdp", {"issuer": "not a url", "clientId": "c", "callbackPort": 0},
     [("xaaIdp.issuer", "Invalid URL"), ("xaaIdp.callbackPort", "Number must be greater than or equal to 0")]),
])
def test_a_wrongly_typed_unread_key_makes_the_whole_file_invalid(key: str, value: Any,
                                                                 expected: list[tuple[str, str]]) -> None:
    """As upstream: the file is ignored as a whole, whatever key fails, read here or not."""
    parsed, errors = _parse({key: value, "permissions": {"allow": ["Bash(ls)"]}})
    assert parsed is None and errors == expected


@pytest.mark.parametrize("document, tip", [
    ({"cleanupPeriodDays": -1}, "Must be 0 or greater. Set a positive number for days to retain transcripts"),
    ({"apiKeyHelper": 1}, "Provide a shell command that outputs your API key to stdout."),
])
def test_the_unread_keys_tips_are_upstreams(document: dict[str, Any], tip: str) -> None:
    errors = parse_settings_document(document, "f.json")[1]
    assert len(errors) == 1 and (errors[0].suggestion or "").startswith(tip)


def test_env_values_are_coerced_to_strings_as_zod() -> None:
    parsed, errors = _parse({"env": {"N": 3, "F": 1.5, "B": False, "Z": None, "L": [1, None, "x"], "O": {"a": 1},
                                     "E": 1e21, "S": "s"}})
    assert errors == [] and parsed == {"env": {"N": "3", "F": "1.5", "B": "false", "Z": "null", "L": "1,,x",
                                               "O": "[object Object]", "E": "1e+21", "S": "s"}}


@pytest.mark.parametrize("value", ["max", 3, None])
def test_an_invalid_effort_level_is_dropped_not_fatal(value: Any) -> None:
    """`effortLevel` is `.catch(undefined)`: an invalid value leaves the key out, the file stays valid."""
    assert _parse({"effortLevel": value, "model": "m"}) == ({"model": "m"}, [])
    assert _parse({"effortLevel": "high"}) == ({"effortLevel": "high"}, [])


@pytest.mark.parametrize("key, value", [
    ("xaaIdp", {"issuer": "https://idp.example", "clientId": "c", "callbackPort": 8080}),
    ("disableDeepLinkRegistration", "disable"), ("voiceEnabled", True), ("defaultView", "chat"),
])
def test_the_gated_keys_whose_code_is_in_the_snapshot_are_declared(key: str, value: Any) -> None:
    assert safe_parse_settings({key: value}, strict=True) == ({key: value}, [])
    _, issues = safe_parse_settings({key: 12345678}, strict=True)
    assert issues and issues[0].path[0] == key and issues[0].code != "unrecognized_keys"


@pytest.mark.parametrize("key", ["envv", "Model", "statusline", "deny", "allow", *GATED_OFF_SETTINGS_KEYS])
def test_strict_parsing_refuses_a_key_upstream_does_not_declare(key: str) -> None:
    _, issues = safe_parse_settings({key: 1}, strict=True)
    assert [(i.code, i.keys) for i in issues] == [("unrecognized_keys", (key,))]
    assert safe_parse_settings({key: 1}) == ({key: 1}, [])  # not strict: passed through, unchecked


def test_an_edit_adding_an_upstream_key_passes_and_a_typo_is_refused(tmp_path: Path) -> None:
    cwd, valid = str(tmp_path), '{"disableAllHooks": true}'
    added = '{"disableAllHooks": true, "env": {"A": "1"}, "model": "opus"}'
    assert validate_input_for_settings_file_edit(".tddagents/settings.json", cwd, None, valid, lambda: added) is None
    typo = validate_input_for_settings_file_edit(".tddagents/settings.json", cwd, None, valid,
                                                 lambda: '{"disableAllHooks": true, "envs": {}}')
    assert typo is not None and "- : Unrecognized field: envs\n" in typo.message


def test_auto_mode_is_validated_like_upstream() -> None:
    rules = {"allow": ["Run the tests"], "soft_deny": ["Push to main"], "environment": ["CI box"]}
    assert _parse({"autoMode": rules}) == ({"autoMode": rules}, [])
    # `z.object` strips unknown keys (the ant-only `deny` alias among them).
    assert _parse({"autoMode": {"allow": ["x"], "deny": ["y"]}}) == ({"autoMode": {"allow": ["x"]}}, [])
    assert _parse({"autoMode": {"allow": "x"}}) == (None, [("autoMode.allow", "Expected array, but received string")])
    assert _parse({"autoMode": {"soft_deny": [1]}}) == (
        None, [("autoMode.soft_deny.0", "Expected string, but received number")])
    assert _parse({"autoMode": ["x"]}) == (None, [("autoMode", "Expected object, but received array")])
    assert safe_parse_settings({"autoMode": {}}, strict=True) == ({"autoMode": {}}, [])


def test_an_invalid_auto_mode_drops_the_file_and_its_classifier_rules(tmp_path: Path) -> None:
    from app.types.permissions import ToolPermissionContext
    from app.utils.permissions.yolo_classifier import AutoModeRules, get_auto_mode_config

    loc = _loc(tmp_path)
    _write(loc.user_config_dir / "settings.json", {"autoMode": {"allow": ["user rule"]}})
    _write(loc.project_root / ".tddagents" / "settings.local.json", {"autoMode": {"environment": "not a list"}})
    context = ToolPermissionContext(settings_locations=loc)
    assert get_auto_mode_config(context) == AutoModeRules(allow=("user rule",))
    _, errors = get_settings_with_errors(loc)
    assert [(e.path, e.message) for e in errors] == [("autoMode.environment", "Expected array, but received string")]


@pytest.mark.parametrize("path, expected", [
    (".tddagents/settings.json", True),
    ("sub/./.TDDAgents/Settings.Local.json", True),
    ("/elsewhere/other/.tddagents/settings.json", True),
    (".tddagents/settings.json.bak", False),
    ("settings.json", False),
    ("../managed/MANAGED-SETTINGS.JSON", True),  # the run's policy file
    ("../flag.json", True),  # the run's --settings file
    ("../flag.json.bak", False),
])
def test_is_tddagents_settings_path(tmp_path: Path, path: str, expected: bool) -> None:
    loc = SettingsLocations(project_root=tmp_path / "project", home=tmp_path / "home",
                            managed_dir=tmp_path / "managed", flag_settings_path=tmp_path / "flag.json")
    assert is_tddagents_settings_path(path, str(tmp_path / "project"), loc) is expected


def test_the_run_settings_paths(tmp_path: Path) -> None:
    loc = _loc(tmp_path)
    assert get_settings_paths(None) == []
    assert get_settings_paths(loc) == [
        str(loc.user_config_dir / "settings.json"), str(loc.project_root / ".tddagents" / "settings.json"),
        str(loc.project_root / ".tddagents" / "settings.local.json"), str(loc.managed_dir / "managed-settings.json")]
    assert is_tddagents_settings_path("x.json", str(tmp_path), None) is False


def test_an_edit_must_keep_a_valid_settings_file_valid(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    valid, invalid = '{"disableAllHooks": true}', '{"disableAllHooks": "no"}'

    def after(text: str) -> Any:
        return lambda: text

    assert validate_input_for_settings_file_edit("notes.json", cwd, None, valid, after(invalid)) is None
    assert validate_input_for_settings_file_edit(".tddagents/settings.json", cwd, None, invalid, after(invalid)) \
        is None  # already invalid: the edit may be the fix
    assert validate_input_for_settings_file_edit(".tddagents/settings.json", cwd, None, valid, after(valid)) is None
    refused = validate_input_for_settings_file_edit(".tddagents/settings.json", cwd, None, valid, after(invalid))
    assert refused is not None and (refused.valid, refused.error_code) == (False, SETTINGS_VALIDATION_ERROR_CODE)
    assert refused.message == (
        "TDDAgents settings.json validation failed after edit:\nSettings validation failed:\n"
        "- disableAllHooks: Expected boolean, but received string\n\nFull schema:\n"
        f"{generate_settings_json_schema()}\nIMPORTANT: Do not update the env unless explicitly instructed to do so.")


def _edit_context(tmp_path: Path, files: dict[str, str]) -> Any:
    from app.state.app_state_store import AppState, AppStateStore
    from app.tool import tool_context_for
    from app.types.permissions import ToolPermissionContext
    from tests.conftest import FakeWorkspace

    permission = ToolPermissionContext(settings_locations=_loc(tmp_path))
    store = AppStateStore(AppState(tool_permission_context=permission))
    return tool_context_for(store, workspace=FakeWorkspace(files=files))


def test_the_edit_tool_refuses_to_break_a_settings_file(tmp_path: Path) -> None:
    from app.tools.file_edit_tool.file_edit_tool import build_edit_tool
    from app.tools.file_read_tool.file_read_tool import build_read_file_tool

    files = {".tddagents/settings.json": '{"disableAllHooks": true, "plansDirectory": "a"}', "a.txt": "true"}
    ctx = _edit_context(tmp_path, files)
    for path in files:
        asyncio.run(build_read_file_tool().call({"file_path": path}, ctx))
    tool = build_edit_tool()
    breaking = {"file_path": ".tddagents/settings.json", "old_string": "true", "new_string": '"yes"'}
    result = tool.validate_input(breaking, ctx)
    assert (result.valid, result.error_code) == (False, SETTINGS_VALIDATION_ERROR_CODE)
    assert "- disableAllHooks: Expected boolean, but received string" in result.message
    fine = {"file_path": ".tddagents/settings.json", "old_string": "true", "new_string": "false"}
    assert tool.validate_input(fine, ctx).valid
    # `replace_all` is simulated too: every occurrence changes.
    files[".tddagents/settings.json"] = '{"disableAllHooks": true, "alwaysThinkingEnabled": true}'
    ctx = _edit_context(tmp_path, files)
    for path in files:
        asyncio.run(build_read_file_tool().call({"file_path": path}, ctx))
    assert tool.validate_input({**breaking, "replace_all": True}, ctx).error_code == SETTINGS_VALIDATION_ERROR_CODE
    one = {"file_path": ".tddagents/settings.json", "old_string": 'Hooks": true', "new_string": 'Hooks": "x"'}
    assert tool.validate_input(one, ctx).error_code == SETTINGS_VALIDATION_ERROR_CODE
    # Not a settings file: no check.
    assert tool.validate_input({"file_path": "a.txt", "old_string": "true", "new_string": "x"}, ctx).valid


# ── the REPL's notice ────────────────────────────────────────────────────────

class _Detector:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


def test_the_settings_errors_notice(tmp_path: Path) -> None:
    loc = _loc(tmp_path)
    out: list[str] = []
    assert use_settings_errors(out.append, None)() is None and out == []
    _write(loc.user_config_dir / "settings.json", {"disableAllHooks": 1, "permissions": {"allow": ["bash"]}})
    detector = _Detector()
    unsubscribe = use_settings_errors(out.append, loc, detector)
    assert out == ["Found 2 settings issues · /doctor for details"]  # the dropped rule's warning counts, as upstream's
    _write(loc.project_root / ".mcp.json", {"mcpServers": {"bad": {"type": "http"}}})
    detector.listeners[0]("userSettings")
    assert out[-1] == "Found 3 settings issues · /doctor for details"
    _write(loc.user_config_dir / "settings.json", {})
    (loc.project_root / ".mcp.json").unlink()
    detector.listeners[0]("userSettings")
    assert len(out) == 2  # no errors: nothing is written
    unsubscribe()
    assert detector.listeners == []
    use_settings_errors(out.append, loc)  # without a detector: the start only
    assert settings_errors_notification_text([ValidationError(path="", message="m")]) == (
        "Found 1 settings issue · /doctor for details")


def test_a_tree_path_part_that_parse_int_reads_shows_the_value() -> None:
    errors = [ValidationError(path="a.1x", message="m", invalid_value=0),
              ValidationError(path="b. 2", message="n", invalid_value=False),
              ValidationError(path="c.x1", message="o", invalid_value="v"),
              ValidationError(path="d.3", message="p")]
    assert build_nested_tree(errors) == {"a": {"0": "m"}, "b": {"false": "n"}, "c": {"x1": "o"}, "d": {"3": "p"}}


def test_a_leading_bom_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"disableAllHooks": true}')
    assert parse_settings_file_with_errors(path) == ({"disableAllHooks": True}, [])
    path.write_bytes(b"\xef\xbb\xbf \n")
    assert parse_settings_file_with_errors(path) == ({}, [])
