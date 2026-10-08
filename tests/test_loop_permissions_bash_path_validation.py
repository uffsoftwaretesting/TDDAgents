"""
`app/loop/permissions/bash_path_validation.py`: port of claude-code's
`tools/BashTool/pathValidation.ts` (legacy path) and `utils/permissions/pathValidation.ts`.
"""

import os
from pathlib import Path

import pytest

from app.loop.permissions import bash_path_validation as pv
from app.loop.permissions.bash_path_validation import (
    ACTION_VERBS,
    COMMAND_OPERATION_TYPE,
    PATH_EXTRACTORS,
    SUPPORTED_PATH_COMMANDS,
    check_command_paths,
    check_dangerous_removal_paths,
    contains_path_traversal,
    expand_tilde,
    filter_out_flags,
    format_directory_list,
    get_glob_base_directory,
    is_dangerous_removal_path,
    parse_command_arguments,
    parse_pattern_command,
    validate_command_paths,
    validate_path,
    validate_single_path_command,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

HOME = str(Path.home())


@pytest.fixture
def cwd(tmp_path):
    return str(tmp_path)


CTX = ToolPermissionContext()
EDITS = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)


# ── vocabularies ─────────────────────────────────────────────────────────────

def test_vocabularies():
    assert SUPPORTED_PATH_COMMANDS == tuple(PATH_EXTRACTORS)
    assert len(SUPPORTED_PATH_COMMANDS) == 36
    assert set(ACTION_VERBS) == set(PATH_EXTRACTORS) == set(COMMAND_OPERATION_TYPE)
    writes = {k for k, v in COMMAND_OPERATION_TYPE.items() if v == "write"}
    creates = {k for k, v in COMMAND_OPERATION_TYPE.items() if v == "create"}
    assert writes == {"rm", "rmdir", "mv", "cp", "sed"}
    assert creates == {"mkdir", "touch"}
    assert ACTION_VERBS["rm"] == "remove files from"
    assert pv.MAX_DIRS_TO_LIST == 5


def test_format_directory_list():
    assert format_directory_list(["/a"]) == "'/a'"
    assert format_directory_list([str(i) for i in range(5)]) == "'0', '1', '2', '3', '4'"
    assert format_directory_list([str(i) for i in range(7)]) == "'0', '1', '2', '3', '4', and 2 more"


def test_expand_tilde_and_traversal_and_glob_base():
    assert expand_tilde("~") == HOME
    assert expand_tilde("~/x") == HOME + "/x"
    assert expand_tilde("~root") == "~root"
    assert expand_tilde("a~") == "a~"
    assert contains_path_traversal("../x") and contains_path_traversal("a/..") and contains_path_traversal("..")
    assert contains_path_traversal("a\\..\\b")
    assert not contains_path_traversal("..a") and not contains_path_traversal("a..")
    assert get_glob_base_directory("src/*.py") == "src"
    assert get_glob_base_directory("*.py") == "."
    assert get_glob_base_directory("/*.py") == "/"
    assert get_glob_base_directory("a/b/c") == "a/b/c"
    assert get_glob_base_directory("a/b{x,y}") == "a"


@pytest.mark.parametrize("path, dangerous", [
    ("*", True), ("/a/*", True), ("/", True), ("//", True), ("/usr", True), ("/tmp/", True), (HOME, True),
    ("C:/", True), ("C:", True), ("C:/Windows", True), ("C:\\\\Windows", True),
    ("/usr/local", False), ("/home/x/project", False), ("C:/a/b", False),
])
def test_is_dangerous_removal_path(path, dangerous):
    assert is_dangerous_removal_path(path) is dangerous


# ── extractors ───────────────────────────────────────────────────────────────

def test_filter_out_flags_honours_double_dash():
    assert filter_out_flags(["-rf", "a", "--", "-/../x", "-y"]) == ["a", "-/../x", "-y"]
    assert filter_out_flags(["", "-a"]) == [""]


@pytest.mark.parametrize("command, args, paths", [
    ("cd", [], [HOME]),
    ("cd", ["my", "dir"], ["my dir"]),
    ("ls", ["-la"], ["."]),
    ("ls", ["a", "b"], ["a", "b"]),
    ("find", [".", "-name", "x"], ["."]),
    ("find", ["-L", "src", "-newer", "f"], ["src", "f"]),
    ("find", ["a", "-newermt", "2020", "-path", "p"], ["a", "2020", "p"]),
    ("find", [], ["."]),
    ("find", ["--", "-/../../etc", "-name"], ["-/../../etc", "-name"]),
    ("find", ["a", "-type", "f", "b"], ["a"]),
    ("find", ["a", "", "-newer"], ["a"]),
    ("tr", ["a", "b", "f"], ["f"]),
    ("tr", ["-d", "a", "f"], ["f"]),
    ("tr", ["-cd", "a", "f"], ["f"]),
    ("grep", ["foo", "a", "b"], ["a", "b"]),
    ("grep", ["-e", "foo", "a"], ["a"]),
    ("grep", ["-r", "foo"], ["."]),
    ("grep", ["foo"], []),
    ("grep", ["-A", "3", "foo", "f"], ["f"]),
    ("grep", ["--include=x", "foo", "f"], ["f"]),
    ("grep", ["--", "-pat", "f"], ["f"]),
    ("rg", ["foo"], ["."]),
    ("rg", ["-g", "*.py", "foo", "src"], ["src"]),
    ("sed", ["s/a/b/", "f"], ["f"]),
    ("sed", ["-f", "script", "f"], ["script", "f"]),
    ("sed", ["-e", "s/a/b/", "f"], ["f"]),
    ("sed", ["-ie", "x", "f"], ["x", "f"]),
    ("sed", ["-n", "1p", "f", "--", "-g"], ["f", "-g"]),
    ("sed", ["-f"], []),
    ("sed", ["", "x", "y"], ["y"]),
    ("jq", [".", "f.json"], ["f.json"]),
    ("jq", ["-e", ".", "f"], ["f"]),                       # -e consumes the filter
    ("jq", ["--arg", "k", "v", ".", "f"], [".", "f"]),     # upstream skips one of --arg's two values
    ("jq", ["--indent=2", ".", "f"], ["f"]),
    ("jq", ["--", ".", "-f"], ["-f"]),
    ("git", ["status"], []),
    ("git", ["diff", "a", "b"], []),
    ("git", ["diff", "--no-index", "a", "b", "c"], ["a", "b"]),
    ("git", [], []),
    ("rm", ["-rf", "x"], ["x"]),
])
def test_path_extractors(command, args, paths):
    assert PATH_EXTRACTORS[command](args) == paths


def test_parse_pattern_command_defaults():
    assert parse_pattern_command(["p"], frozenset(), ["."]) == ["."]
    assert parse_pattern_command(["p", "f"], frozenset(), ["."]) == ["f"]
    assert parse_pattern_command(["-f", "pats", "f"], frozenset({"-f"})) == ["f"]


# ── validate_path ────────────────────────────────────────────────────────────

def test_validate_path_inside_and_outside(cwd):
    assert validate_path("a.txt", cwd, CTX, "read") == (True, os.path.join(cwd, "a.txt"), None)
    assert validate_path("'a.txt'", cwd, CTX, "read")[0] is True
    allowed, resolved, reason = validate_path("/etc/passwd", cwd, CTX, "read")
    assert (allowed, resolved, reason) == (False, "/etc/passwd", None)
    assert validate_path("a.txt", cwd, CTX, "write")[0] is False       # writes need acceptEdits
    assert validate_path("a.txt", cwd, EDITS, "create") == (True, os.path.join(cwd, "a.txt"), None)


@pytest.mark.parametrize("path, reason", [
    ("~root/.ssh", "Tilde expansion variants (~user, ~+, ~-) in paths require manual approval"),
    ("~+", "Tilde expansion variants (~user, ~+, ~-) in paths require manual approval"),
    ("$HOME/x", "Shell expansion syntax in paths requires manual approval"),
    ("%TEMP%", "Shell expansion syntax in paths requires manual approval"),
    ("=rg", "Shell expansion syntax in paths requires manual approval"),
])
def test_validate_path_rejections(cwd, path, reason):
    assert validate_path(path, cwd, CTX, "read") == (False, path, {"type": "other", "reason": reason})


def test_validate_path_globs(cwd):
    reason = {"type": "other",
              "reason": "Glob patterns are not allowed in write operations. Please specify an exact file path."}
    assert validate_path("*.txt", cwd, EDITS, "write") == (False, "*.txt", reason)
    assert validate_path("*.txt", cwd, EDITS, "create")[2] == reason
    assert validate_path("src/*.py", cwd, CTX, "read") == (True, os.path.join(cwd, "src"), None)
    assert validate_path("/etc/*.conf", cwd, CTX, "read") == (False, "/etc", None)
    assert validate_path("../*", cwd, CTX, "read") == (False, os.path.dirname(cwd) + "/*", None)


def test_validate_path_tilde_home_and_unc(cwd, monkeypatch):
    assert validate_path("~", cwd, CTX, "read") == (False, HOME, None)
    monkeypatch.setattr(pv, "contains_vulnerable_unc_path", lambda s: True)
    assert validate_path("x", cwd, CTX, "read") == (
        False, "x", {"type": "other", "reason": "UNC network paths require manual approval"})


def test_validate_path_carries_rule_and_safety_reasons(cwd):
    rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.DENY,
                          rule_content=os.path.join(cwd, "secret"), source=PermissionRuleSource.USER_SETTINGS)
    ctx = ToolPermissionContext(always_deny_rules=(rule,))
    allowed, _, reason = validate_path("secret", cwd, ctx, "read")
    assert allowed is False and reason == {"type": "rule", "rule": rule}
    allowed, _, reason = validate_path(".git/config", cwd, EDITS, "write")
    assert allowed is False and reason is not None and reason["type"] == "safetyCheck"


# ── validate_command_paths / check_command_paths ─────────────────────────────

def test_validate_command_paths_mv_cp_flags(cwd):
    res = validate_command_paths("cp", ["-t", "/x", "a"], cwd, EDITS)
    assert res.behavior == "ask"
    assert res.message == (
        "cp with flags requires manual approval to ensure path safety. For security, TDDAgents cannot "
        "automatically validate cp commands that use flags, as some flags like --target-directory=PATH can "
        "bypass path validation.")
    assert res.decision_reason == {"type": "other", "reason": "cp command with flags requires manual approval"}
    assert validate_command_paths("mv", ["a", "b"], cwd, EDITS).behavior == "passthrough"


def test_validate_command_paths_cd_with_writes(cwd):
    res = validate_command_paths("rm", ["x"], cwd, EDITS, compound_command_has_cd=True)
    assert res.behavior == "ask"
    assert res.decision_reason == {
        "type": "other",
        "reason": "Compound command contains cd with write operation - manual approval required to prevent path "
                  "resolution bypass"}
    assert validate_command_paths("cat", ["x"], cwd, CTX, compound_command_has_cd=True).behavior == "passthrough"


def test_validate_command_paths_blocked_message(cwd):
    res = validate_command_paths("cat", ["/etc/passwd"], cwd, ToolPermissionContext(
        additional_working_directories=("/srv",)))
    assert res.behavior == "ask"
    assert res.message == (
        f"cat in '/etc/passwd' was blocked. For security, TDDAgents may only concatenate files from the allowed "
        f"working directories for this session: '{cwd}', '/srv'.")
    ok = validate_command_paths("cat", ["a"], cwd, CTX)
    assert (ok.behavior, ok.message) == ("passthrough", "Path validation passed for cat command")
    other = validate_command_paths("cat", ["$X"], cwd, CTX)
    assert other.message == "Shell expansion syntax in paths requires manual approval"


def test_validate_command_paths_deny_rule(cwd):
    rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.DENY,
                          rule_content=os.path.join(cwd, "s"), source=PermissionRuleSource.USER_SETTINGS)
    res = validate_command_paths("cat", ["s"], cwd, ToolPermissionContext(always_deny_rules=(rule,)))
    assert res.behavior == "deny"
    assert res.message.startswith(f"cat in '{os.path.join(cwd, 's')}' was blocked.")


def test_operation_override(cwd):
    assert validate_command_paths("sed", ["s/a/b/", "f"], cwd, CTX).behavior == "ask"
    assert validate_command_paths("sed", ["s/a/b/", "f"], cwd, CTX, operation_override="read").behavior == (
        "passthrough")


def test_dangerous_removals(cwd):
    res = check_dangerous_removal_paths("rm", ["-rf", "/"], cwd)
    assert res.behavior == "ask"
    assert res.message == (
        "Dangerous rm operation detected: '/'\n\nThis command would remove a critical system directory. This "
        "requires explicit approval and cannot be auto-allowed by permission rules.")
    assert res.decision_reason == {"type": "other", "reason": "Dangerous rm operation on critical path: /"}
    assert check_dangerous_removal_paths("rmdir", ["'~'"], cwd).behavior == "ask"
    assert check_dangerous_removal_paths("rm", ["*"], cwd).behavior == "ask"   # resolves to cwd/*
    ok = check_dangerous_removal_paths("rm", ["x"], cwd)
    assert (ok.behavior, ok.message) == ("passthrough", "No dangerous removals detected for rm command")


def test_check_command_paths_order(cwd):
    # dangerous removal beats acceptEdits allowing the write
    assert check_command_paths("rm", ["/usr"], cwd, EDITS).message.startswith("Dangerous rm operation")
    rule = PermissionRule(tool_name="Bash", rule_behavior=PermissionBehavior.DENY, rule_content="/usr",
                          source=PermissionRuleSource.USER_SETTINGS)
    # an explicit deny rule wins over the dangerous-path message
    assert check_command_paths("rm", ["/usr"], cwd, ToolPermissionContext(always_deny_rules=(rule,))).behavior == (
        "deny")
    assert check_command_paths("rm", ["x"], cwd, EDITS).behavior == "passthrough"
    assert check_command_paths("cat", ["/etc/x"], cwd, CTX).behavior == "ask"


# ── parse_command_arguments / validate_single_path_command ───────────────────

def test_parse_command_arguments():
    assert parse_command_arguments("rm -rf 'a b' *.txt $X") == ["rm", "-rf", "a b", "*.txt", "$X"]
    assert parse_command_arguments('grep "" f') == ["grep", "", "f"]
    assert parse_command_arguments("ls | wc") == ["ls", "wc"]
    assert parse_command_arguments("echo ${}") == []


def test_validate_single_path_command(cwd):
    assert validate_single_path_command("", cwd, CTX).message == "Empty command - no paths to validate"
    res = validate_single_path_command("python x.py", cwd, CTX)
    assert (res.behavior, res.message) == ("passthrough", "Command 'python' is not a path-restricted command")
    assert validate_single_path_command("timeout 5 rm /etc/x", cwd, CTX).message.startswith("rm in '/etc/x'")
    assert validate_single_path_command("nice rm /usr", cwd, CTX).message.startswith("Dangerous rm")
    assert validate_single_path_command("cat a", cwd, CTX).behavior == "passthrough"
    # read-only sed validates its files as reads
    assert validate_single_path_command("sed -n '1p' f", cwd, CTX).behavior == "passthrough"
    assert validate_single_path_command("sed -i 's/a/b/' f", cwd, CTX).behavior == "ask"
    assert validate_single_path_command("rm -- -/../x", cwd, EDITS).behavior == "passthrough"  # stays inside
    assert validate_single_path_command("rm -- -/../../x", cwd, EDITS).behavior == "ask"
    assert validate_single_path_command("'' x", cwd, CTX).message == "Command '' is not a path-restricted command"
