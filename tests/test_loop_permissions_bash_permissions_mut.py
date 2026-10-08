"""
Mutation-driven pins for `bash_permissions.py` and `bash_path_validation.py`.

Each test pins a behavior upstream claude-code has (`bashPermissions.ts`,
`bashCommandHelpers.ts`, `pathValidation.ts`) that the general suites left implicit:
full results (behavior, message, decision reason, updated input) rather than behavior alone,
argument pass-through (cwd, compound-cd, match mode), and loop/branch edges.
"""

from __future__ import annotations

import pytest

from app.loop.permissions import bash_path_validation as pv
from app.loop.permissions import bash_permissions as bp
from app.loop.permissions import bash_read_only_commands as roc
from app.loop.permissions.bash_commands import OutputRedirection
from app.loop.permissions.bash_security import bash_command_is_safe
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

ALLOW, DENY, ASK, PASS = (PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK,
                          PermissionBehavior.PASSTHROUGH)
REQUEST = "The agent requested permissions to use Bash, but you haven't granted it yet."
CD_REDIRECT_MSG = (
    "Commands that change directories and write via output redirection require explicit approval to ensure "
    "paths are evaluated correctly. For security, TDDAgents cannot automatically determine the final working "
    "directory when 'cd' is used in compound commands."
)
CD_WRITE_MSG = (
    "Commands that change directories and perform write operations require explicit approval to ensure paths "
    "are evaluated correctly. For security, TDDAgents cannot automatically determine the final working "
    "directory when 'cd' is used in compound commands."
)
HEREDOC = "$(cat <<'EOF'\nhello\nEOF\n)"


def rule(behavior, content):
    return PermissionRule(tool_name="Bash", rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.PROJECT_SETTINGS)


def ctx(*rules, mode=PermissionMode.DEFAULT):
    return ToolPermissionContext(
        mode=mode,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == ASK),
    )


@pytest.fixture(autouse=True)
def injection_checks_on(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", raising=False)


# ── rule matching ────────────────────────────────────────────────────────────

def test_exact_match_uses_the_command_with_its_redirection():
    r = rule(ALLOW, "echo hi > /dev/null")
    res = bp.bash_tool_check_exact_match_permission("echo hi > /dev/null", ctx(r))
    assert (res.behavior, res.decision_reason) == (ALLOW, {"type": "rule", "rule": r})
    assert res.updated_input == {"command": "echo hi > /dev/null"}


def test_exact_mode_prefix_rule_matches_a_stripped_wrapper_candidate():
    r = rule(ALLOW, "npm:*")
    assert bp.bash_tool_check_exact_match_permission("timeout 5 npm", ctx(r)).behavior == ALLOW


def test_exact_mode_does_not_treat_prefix_rules_as_prefixes():
    for behavior in (DENY, ASK):
        res = bp.bash_tool_check_exact_match_permission("rm x", ctx(rule(behavior, "rm:*")))
        assert res.behavior == PASS


def test_prefix_wildcard_rules_skip_compound_commands():
    r = rule(ALLOW, "git *")
    assert bp.filter_rules_by_contents_matching_input("git a && rm b", {"git *": r}, "prefix") == []
    assert bp.filter_rules_by_contents_matching_input("git a", {"git *": r}, "prefix") == [r]


def test_deny_and_ask_rules_match_compound_commands_and_strip_env_vars():
    d, a = rule(DENY, "rm:*"), rule(ASK, "curl:*")
    deny, ask, _ = bp.matching_rules_for_input("rm x && ls", ctx(d), "prefix")
    assert deny == [d]
    _, ask, _ = bp.matching_rules_for_input("curl x && ls", ctx(a), "prefix")
    assert ask == [a]
    _, ask, _ = bp.matching_rules_for_input("FOO=1 curl x", ctx(a), "prefix")
    assert ask == [a]


def test_allow_rules_honour_skip_compound_check():
    r = rule(ALLOW, "ls:*")
    assert bp.matching_rules_for_input("ls && pwd", ctx(r), "prefix")[2] == []
    assert bp.matching_rules_for_input("ls && pwd", ctx(r), "prefix", skip_compound_check=True)[2] == [r]


def test_rule_results_are_complete(tmp_path):
    cwd = str(tmp_path)
    d, a, al = rule(DENY, "rm:*"), rule(ASK, "curl:*"), rule(ALLOW, "make:*")
    res = bp.bash_tool_check_permission("  rm x  ", ctx(d), cwd)
    assert res == PermissionResult(behavior=DENY, message="Permission to use Bash with command rm x has been denied.",
                                   decision_reason={"type": "rule", "rule": d})
    res = bp.bash_tool_check_permission("curl x", ctx(a), cwd)
    assert res == PermissionResult(behavior=ASK, message=REQUEST, decision_reason={"type": "rule", "rule": a})
    res = bp.bash_tool_check_permission("make all", ctx(al), cwd)
    assert res == PermissionResult(behavior=ALLOW, updated_input={"command": "make all"},
                                   decision_reason={"type": "rule", "rule": al})


# ── mode ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode, message", [
    (PermissionMode.BYPASS_PERMISSIONS, "Bypass mode is handled in main permission flow"),
    (PermissionMode.DONT_ASK, "DontAsk mode is handled in main permission flow"),
])
def test_check_permission_mode_passthrough_modes(mode, message):
    assert bp.check_permission_mode("mkdir d", ctx(mode=mode)) == PermissionResult(behavior=PASS, message=message)


# ── output redirections and path constraints ────────────────────────────────

def test_cd_with_redirection_message_is_exact(tmp_path):
    res = bp.validate_output_redirections((OutputRedirection("f", ">"),), str(tmp_path), ctx(), True)
    assert res.behavior == ASK and res.message == CD_REDIRECT_MSG
    res = bp.check_path_constraints("echo x > f", str(tmp_path), ctx(), True)
    assert res.message == CD_REDIRECT_MSG


def test_dev_null_and_allowed_targets_do_not_stop_the_scan(tmp_path):
    cwd = str(tmp_path)
    res = bp.validate_output_redirections(
        (OutputRedirection("/dev/null", ">"), OutputRedirection("/etc/x", ">")), cwd, ctx())
    assert res.behavior == ASK and "/etc/x" in res.message
    accept = ctx(mode=PermissionMode.ACCEPT_EDITS)
    res = bp.validate_output_redirections(
        (OutputRedirection("ok.txt", ">"), OutputRedirection("/etc/x", ">")), cwd, accept)
    assert res.behavior == ASK and "/etc/x" in res.message


def test_redirection_deny_rule_and_safety_reasons(tmp_path):
    cwd = str(tmp_path)
    target = str(tmp_path / "secret.txt")
    path_rule = PermissionRule(tool_name="Bash", rule_behavior=DENY, rule_content=target)
    res = bp.validate_output_redirections((OutputRedirection(target, ">"),), cwd, ctx(path_rule))
    assert res == PermissionResult(behavior=DENY,
                                   message=f"Output redirection to '{target}' was blocked by a deny rule.",
                                   decision_reason={"type": "rule", "rule": path_rule})
    reason = "Modifying files inside sensitive directory '.git' is restricted."
    res = bp.validate_output_redirections((OutputRedirection(".git/config", ">"),), cwd,
                                          ctx(mode=PermissionMode.ACCEPT_EDITS))
    assert res == PermissionResult(behavior=ASK, message=reason,
                                   decision_reason={"type": "safetyCheck", "reason": reason})


def test_process_substitution_and_dangerous_redirection_results(tmp_path):
    cwd = str(tmp_path)
    assert bp.check_path_constraints("diff <(ls) b", cwd, ctx()) == PermissionResult(
        behavior=ASK,
        message="Process substitution (>(...) or <(...)) can execute arbitrary commands and requires manual approval",
        decision_reason={"type": "other", "reason": "Process substitution requires manual approval"})
    reason = "Shell expansion syntax in paths requires manual approval"
    assert bp.check_path_constraints("echo x > $HOME/f", cwd, ctx()) == PermissionResult(
        behavior=ASK, message=reason, decision_reason={"type": "other", "reason": reason})


def test_compound_cd_reaches_the_path_checks(tmp_path):
    cwd = str(tmp_path)
    assert bp.check_path_constraints("mkdir d", cwd, ctx(), True).message == CD_WRITE_MSG
    assert bp.bash_tool_check_permission("mkdir d", ctx(), cwd, True).message == CD_WRITE_MSG
    assert bp.check_command_and_suggest_rules("mkdir d", ctx(), cwd, True).message == CD_WRITE_MSG


def test_read_only_allow_result_and_cwd_bare_repo(tmp_path):
    cwd = str(tmp_path)
    assert bp.bash_tool_check_permission("ls", ctx(), cwd) == PermissionResult(
        behavior=ALLOW, updated_input={"command": "ls"},
        decision_reason={"type": "other", "reason": "Read-only command is allowed"})
    (tmp_path / "HEAD").write_text("ref: x")
    assert bp.bash_tool_check_permission("git status", ctx(), cwd).behavior == PASS


# ── command identity ─────────────────────────────────────────────────────────

def test_empty_command_is_neither_git_nor_cd():
    assert bp.is_normalized_git_command("") is False
    assert bp.is_normalized_cd_command("") is False


# ── pipes ────────────────────────────────────────────────────────────────────

def test_pipe_results_are_complete(tmp_path):
    cwd = str(tmp_path)
    res = bp.bash_tool_has_permission("ls | wc -l", ctx(), cwd)
    assert res.behavior == ALLOW and res.updated_input == {"command": "ls | wc -l"}
    assert res.decision_reason is not None and set(res.decision_reason) == {"type", "reasons"}
    assert res.decision_reason["type"] == "subcommandResults"
    d = rule(DENY, "rm:*")
    res = bp.bash_tool_has_permission("ls | rm x", ctx(d), cwd)
    assert res.behavior == DENY and res.decision_reason is not None
    assert set(res.decision_reason) == {"type", "reasons"} and res.decision_reason["type"] == "subcommandResults"
    res = bp.bash_tool_has_permission("ls | pip install x", ctx(), cwd)
    assert res.behavior == ASK
    assert res.decision_reason is not None and res.decision_reason["type"] == "subcommandResults"
    assert list(res.decision_reason["reasons"]) == ["ls", "pip install x"]
    assert res.message == ("This Bash command contains multiple operations. The following part requires approval: "
                           "pip install x")


def test_pipe_with_git_but_no_cd_is_not_the_cd_git_ask(tmp_path):
    res = bp.bash_tool_has_permission("git log | head", ctx(), str(tmp_path))
    assert res.message != "Compound commands with cd and git require approval to prevent bare repository attacks"
    res = bp._segmented_command_permission_result("ls | cat", ["ls", "cat"], ctx(), str(tmp_path))
    assert res.behavior == ALLOW


def test_allowed_pipe_still_gets_whole_command_path_checks(tmp_path, monkeypatch):
    """The security battery already asks for `>`; the path check shows once it is disabled."""
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", "1")
    cwd = str(tmp_path)
    res = bp.bash_tool_has_permission("ls | wc > /etc/x", ctx(), cwd)
    assert res.behavior == ASK and "/etc/x" in res.message
    res = bp.bash_tool_has_permission("cd sub | ls > out.txt", ctx(), cwd)
    assert res.message == CD_REDIRECT_MSG


def test_allowed_pipe_still_gets_the_whole_command_security_check(tmp_path, monkeypatch):
    cwd = str(tmp_path)

    real = bash_command_is_safe

    def flag_whole(command):
        if command == "ls | wc -l":
            return PermissionResult(behavior=ASK, message="")
        return real(command)

    monkeypatch.setattr(bp, "bash_command_is_safe", flag_whole)
    res = bp.bash_tool_has_permission("ls | wc -l", ctx(), cwd)
    assert res.behavior == ASK and res.message == "Command contains patterns that require approval"


# ── misparsing / heredoc branch ──────────────────────────────────────────────

def test_safe_heredoc_remainder_does_not_ask_early(tmp_path):
    cmd = f'printf "%s" "{HEREDOC}" && ls'
    assert bash_command_is_safe(cmd).is_bash_security_check_for_misparsing is True
    res = bp.bash_tool_has_permission(cmd, ctx(), str(tmp_path))
    assert res.behavior == PASS
    assert res.message.startswith("This Bash command contains multiple operations. The following part requires")


def test_non_misparsing_remainder_does_not_ask_early(tmp_path):
    cmd = f'printf "%s" "{HEREDOC}" > out.txt'
    res = bp.bash_tool_has_permission(cmd, ctx(), str(tmp_path))
    assert res.message != bash_command_is_safe(cmd).message


def test_misparsing_compound_command_asks_with_the_validator_message(tmp_path):
    cmd = "echo \x01 && ls"
    original = bash_command_is_safe(cmd)
    res = bp.bash_tool_has_permission(cmd, ctx(), str(tmp_path))
    assert res == PermissionResult(behavior=ASK, message=original.message,
                                   decision_reason={"type": "other", "reason": original.message})


def test_disabled_injection_checks_skip_the_misparsing_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", "1")
    assert bp.bash_tool_has_permission("echo `id`", ctx(), str(tmp_path)).behavior == PASS


# ── compound commands ────────────────────────────────────────────────────────

def test_cd_rules_and_messages(tmp_path):
    cwd = str(tmp_path)
    (tmp_path / "sub").mkdir()
    assert bp.bash_tool_has_permission("cd sub", ctx(), cwd).behavior == ALLOW
    assert bp.bash_tool_has_permission("cd a && cd b", ctx(), cwd).message == (
        "Multiple directory changes in one command require approval for clarity")
    assert bp.bash_tool_has_permission("cd a && git status", ctx(), cwd).message == (
        "Compound commands with cd and git require approval to prevent bare repository attacks")
    assert bp.bash_tool_has_permission("cd sub && mkdir d", ctx(), cwd).message == CD_WRITE_MSG


def test_compound_deny_result_shape(tmp_path):
    d = rule(DENY, "rm:*")
    res = bp.bash_tool_has_permission("ls && rm x", ctx(d), str(tmp_path))
    assert res.behavior == DENY
    assert res.message == "Permission to use Bash with command ls && rm x has been denied."
    assert res.decision_reason is not None and set(res.decision_reason) == {"type", "reasons"}
    assert res.decision_reason["type"] == "subcommandResults"
    assert list(res.decision_reason["reasons"]) == ["ls", "rm x"]


def test_single_ask_among_allows_is_returned_directly(tmp_path):
    cwd = str(tmp_path)
    for cmd in ("ls && rm -rf /", "ls && pwd && rm -rf /"):
        res = bp.bash_tool_has_permission(cmd, ctx(), cwd)
        assert res.behavior == ASK
        assert res.message.startswith("Dangerous rm operation detected: '/'")


def test_all_allowed_compound_result(tmp_path):
    res = bp.bash_tool_has_permission("ls && pwd", ctx(), str(tmp_path))
    assert res.behavior == ALLOW and res.updated_input == {"command": "ls && pwd"}
    assert res.decision_reason is not None and res.decision_reason["type"] == "subcommandResults"
    single = bp.bash_tool_has_permission("ls", ctx(), str(tmp_path))
    assert single.decision_reason is not None and single.decision_reason["type"] == "subcommandResults"


def test_multiple_unresolved_subcommands_pass_through(tmp_path):
    res = bp.bash_tool_has_permission("pip install a && pip install b", ctx(), str(tmp_path))
    assert res.behavior == PASS
    assert res.decision_reason is not None and res.decision_reason["type"] == "subcommandResults"
    assert list(res.decision_reason["reasons"]) == ["pip install a", "pip install b"]
    assert res.message == ("This Bash command contains multiple operations. The following parts require approval: "
                           "pip install a, pip install b")


# ── bash_path_validation ─────────────────────────────────────────────────────

def test_validate_path_strips_quotes(tmp_path):
    allowed, resolved, _ = pv.validate_path("'a.txt'", str(tmp_path), ctx(), "read")
    assert (allowed, resolved) == (True, str(tmp_path / "a.txt"))


def test_validate_path_unc_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(roc, "get_platform", lambda: "windows")
    assert pv.validate_path("//server/share/x", str(tmp_path), ctx(), "read") == (
        False, "//server/share/x", {"type": "other", "reason": "UNC network paths require manual approval"})


@pytest.mark.parametrize("path", ["c:/", "c:/Windows", "C:/"])
def test_drive_roots_and_children_are_dangerous(path):
    assert pv.is_dangerous_removal_path(path) is True


@pytest.mark.parametrize("args, expected", [
    (["--regexp=foo", "file"], ["file"]),
    (["--regexp", "foo", "file"], ["file"]),
    (["--file", "pats", "file"], ["file"]),
    (["--file=pats", "file"], ["file"]),
    (["pat", "-m", "5", "f"], ["f"]),
])
def test_parse_pattern_command_flags(args, expected):
    assert pv.parse_pattern_command(args, pv._GREP_FLAGS_WITH_ARGS) == expected


@pytest.mark.parametrize("args, expected", [
    (["", "dir"], ["dir"]),
    (["-H", "dir"], ["dir"]),
    (["-P", "dir"], ["dir"]),
    (["-newerBt", "ref", "dir"], ["ref"]),
])
def test_extract_find_edges(args, expected):
    assert pv._extract_find(args) == expected


def test_extract_tr_counts_only_flags_as_delete():
    assert pv._extract_tr(["abd", "xyz", "f"]) == ["f"]


@pytest.mark.parametrize("flag", ["-R", "--recursive"])
def test_extract_grep_recursive_defaults_to_cwd(flag):
    assert pv._extract_grep([flag, "pat"]) == ["."]


def test_extract_sed_expression_and_combined_flags():
    assert pv._extract_sed(["--expression", "s/a/b/", "file"]) == ["file"]
    assert pv._extract_sed(["-nf", "x", "file"]) == ["x", "file"]


@pytest.mark.parametrize("args, expected", [
    (["--expression=.a", "file"], ["file"]),
    (["--expression", ".a", "f"], ["f"]),
    ([".a", "--indent", "2", "f"], ["f"]),
])
def test_extract_jq_flags(args, expected):
    assert pv._extract_jq(args) == expected


def test_extract_git_no_index_keeps_paths_before_the_flag():
    assert pv._extract_git(["diff", "a", "--no-index", "b"]) == ["a", "b"]


def test_dangerous_removal_resolves_relative_to_cwd():
    res = pv.check_dangerous_removal_paths("rm", ["tmp"], "/")
    assert res.behavior == ASK and res.message.startswith("Dangerous rm operation detected: '/tmp'")


def test_validate_command_paths_scans_every_path_and_keeps_reasons(tmp_path):
    cwd = str(tmp_path)
    accept = ctx(mode=PermissionMode.ACCEPT_EDITS)
    res = pv.validate_command_paths("mkdir", ["ok", "/etc/x"], cwd, accept)
    assert res.behavior == ASK and "/etc/x" in res.message
    reason = "Modifying files inside sensitive directory '.git' is restricted."
    assert pv.validate_command_paths("touch", [".git/x"], cwd, accept) == PermissionResult(
        behavior=ASK, message=reason, decision_reason={"type": "safetyCheck", "reason": reason})
    assert pv.validate_command_paths("mkdir", ["d"], cwd, ctx(), True) == PermissionResult(
        behavior=ASK, message=CD_WRITE_MSG,
        decision_reason={"type": "other", "reason": "Compound command contains cd with write operation - manual "
                                                    "approval required to prevent path resolution bypass"})


def test_check_command_paths_compound_and_rmdir(tmp_path):
    cwd = str(tmp_path)
    assert pv.check_command_paths("mkdir", ["d"], cwd, ctx(), True).message == CD_WRITE_MSG
    res = pv.check_command_paths("rmdir", ["/usr"], cwd, ctx(mode=PermissionMode.BYPASS_PERMISSIONS))
    assert res.behavior == ASK and res.message.startswith("Dangerous rmdir operation detected: '/usr'")


def test_validate_single_path_command_empty_and_compound(tmp_path):
    cwd = str(tmp_path)
    assert pv.validate_single_path_command("", cwd, ctx()) == PermissionResult(
        behavior=PASS, message="Empty command - no paths to validate")
    assert pv.validate_single_path_command("mkdir d", cwd, ctx(), True).message == CD_WRITE_MSG
