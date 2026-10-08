"""
`app/loop/permissions/bash_permissions.py`: port of claude-code's `bashToolHasPermission`
flow (bashPermissions.ts, bashCommandHelpers.ts, modeValidation.ts, the path checks).
"""

import os

import pytest

from app.loop.permissions import bash_permissions as bp
from app.loop.permissions.bash_permissions import (
    ACCEPT_EDITS_ALLOWED_COMMANDS,
    ANT_ONLY_SAFE_ENV_VARS,
    BASH_TOOL_NAME,
    SAFE_ENV_VARS,
    bash_tool_check_exact_match_permission,
    bash_tool_check_permission,
    bash_tool_has_permission,
    check_command_and_suggest_rules,
    check_command_operator_permissions,
    check_path_constraints,
    check_permission_mode,
    command_has_any_cd,
    filter_rules_by_contents_matching_input,
    get_rule_by_contents_for_tool,
    is_normalized_cd_command,
    is_normalized_git_command,
    matching_rules_for_input,
    request_message,
    strip_all_leading_env_vars,
    strip_comment_lines,
    strip_safe_wrappers,
    validate_output_redirections,
)
from app.loop.permissions.bash_commands import OutputRedirection
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)


def rule(content, behavior=PermissionBehavior.ALLOW, tool="Bash"):
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content,
                          source=PermissionRuleSource.PROJECT_SETTINGS)


def ctx(allow=(), deny=(), ask=(), mode=PermissionMode.DEFAULT):
    return ToolPermissionContext(
        mode=mode,
        always_allow_rules=tuple(rule(c) for c in allow),
        always_deny_rules=tuple(rule(c, PermissionBehavior.DENY) for c in deny),
        always_ask_rules=tuple(rule(c, PermissionBehavior.ASK) for c in ask),
    )


@pytest.fixture
def cwd(tmp_path):
    return str(tmp_path)


def test_vocabularies():
    assert BASH_TOOL_NAME == "Bash"
    assert ACCEPT_EDITS_ALLOWED_COMMANDS == ("mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed")
    assert "PATH" not in SAFE_ENV_VARS and "LD_PRELOAD" not in SAFE_ENV_VARS
    assert {"NODE_ENV", "PYTHONUNBUFFERED", "LANG"} <= SAFE_ENV_VARS
    assert "GH_TOKEN" in ANT_ONLY_SAFE_ENV_VARS and "GH_TOKEN" not in SAFE_ENV_VARS


# ── request_message ──────────────────────────────────────────────────────────

def test_request_message():
    assert request_message() == "The agent requested permissions to use Bash, but you haven't granted it yet."
    assert request_message("Bash", {"type": "other", "reason": "R"}) == "R"
    assert request_message("Bash", {"type": "safetyCheck", "reason": "S"}) == "S"
    ask = PermissionResult(behavior=PermissionBehavior.ASK)
    allow = PermissionResult(behavior=PermissionBehavior.ALLOW)
    one = {"type": "subcommandResults", "reasons": {"rm x > out": ask, "ls": allow}}
    assert request_message("Bash", one) == (
        "This Bash command contains multiple operations. The following part requires approval: rm x")
    two = {"type": "subcommandResults", "reasons": {"a": ask, "b": PermissionResult(PermissionBehavior.PASSTHROUGH)}}
    assert request_message("Bash", two) == (
        "This Bash command contains multiple operations. The following parts require approval: a, b")
    assert request_message("Other", {"type": "subcommandResults", "reasons": {"x > y": ask}}).endswith("x > y")
    none = {"type": "subcommandResults", "reasons": {"a": allow}}
    assert request_message("Bash", none) == "This Bash command contains multiple operations that require approval"
    assert request_message("Bash", {"type": "subcommandResults", "reasons": "bad"}) == (
        "This Bash command contains multiple operations that require approval")
    assert request_message("Bash", {"type": "rule"}).startswith("The agent requested")


# ── stripping ────────────────────────────────────────────────────────────────

def test_strip_comment_lines():
    assert strip_comment_lines("# c\nls\n\n  # d\npwd") == "ls\npwd"
    assert strip_comment_lines("# only\n# comments") == "# only\n# comments"


@pytest.mark.parametrize("command, stripped", [
    ("NODE_ENV=prod npm test", "npm test"),
    ("LANG=C LC_ALL=C sort f", "sort f"),
    ("PATH=/evil ls", "PATH=/evil ls"),             # not a safe env var
    ("timeout 10 rm x", "rm x"),
    ("timeout --signal=KILL 5s ls", "ls"),
    ("timeout -k 5 10 ls", "ls"),
    ("time ls", "ls"),
    ("nice -n 5 ls", "ls"),
    ("nice ls", "ls"),
    ("stdbuf -oL ls", "ls"),
    ("nohup ls", "ls"),
    ("nohup -- ls", "ls"),
    ("timeout 5 NODE_ENV=x ls", "NODE_ENV=x ls"),   # env after a wrapper is the command
    ("# c\nNODE_ENV=x ls", "ls"),
    ("  ls  ", "ls"),
])
def test_strip_safe_wrappers(command, stripped):
    assert strip_safe_wrappers(command) == stripped


def test_strip_safe_wrappers_ant_only(monkeypatch):
    assert strip_safe_wrappers("GH_TOKEN=x gh pr list") == "GH_TOKEN=x gh pr list"
    monkeypatch.setenv("USER_TYPE", "ant")
    assert strip_safe_wrappers("GH_TOKEN=x gh pr list") == "gh pr list"


@pytest.mark.parametrize("command, stripped", [
    ("PATH=/x rm -rf y", "rm -rf y"),
    ("A=1 B='two words' C=\"x\" rm y", "rm y"),
    ("A+=1 rm y", "rm y"),
    ("ARR[0]=1 rm y", "rm y"),
    ("A=$(id) rm y", "A=$(id) rm y"),              # substitution is not stripped
    ("rm y", "rm y"),
])
def test_strip_all_leading_env_vars(command, stripped):
    assert strip_all_leading_env_vars(command) == stripped


def test_strip_all_leading_env_vars_blocklist():
    import re
    assert strip_all_leading_env_vars("LD_PRELOAD=x A=1 rm y", re.compile("^LD_")) == "LD_PRELOAD=x A=1 rm y"


# ── rule matching ────────────────────────────────────────────────────────────

def test_get_rule_by_contents_for_tool():
    c = ToolPermissionContext(always_allow_rules=(rule("ls"), rule(None), rule("x", tool="Read")))
    assert list(get_rule_by_contents_for_tool(c, "Bash", PermissionBehavior.ALLOW)) == ["ls"]
    assert get_rule_by_contents_for_tool(c, "Bash", PermissionBehavior.DENY) == {}


def _match(command, contents, mode, **kw):
    rules = {c: rule(c) for c in contents}
    return [r.rule_content for r in filter_rules_by_contents_matching_input(command, rules, mode, **kw)]


@pytest.mark.parametrize("command, contents, mode, matched", [
    ("npm install", ["npm install"], "exact", ["npm install"]),
    ("npm install x", ["npm install"], "exact", []),
    ("npm install x", ["npm:*"], "prefix", ["npm:*"]),
    ("npm", ["npm:*"], "prefix", ["npm:*"]),
    ("npmx", ["npm:*"], "prefix", []),
    ("npm", ["npm:*"], "exact", ["npm:*"]),
    ("npm x", ["npm:*"], "exact", []),
    ("xargs npm x", ["npm:*"], "prefix", ["npm:*"]),
    ("npm x && rm y", ["npm:*"], "prefix", []),       # compound commands never prefix-match
    ("git status --short", ["git status *"], "prefix", ["git status *"]),
    ("git status", ["git status *"], "prefix", ["git status *"]),
    ("git status", ["git status *"], "exact", []),
    ("timeout 5 npm test", ["npm test"], "exact", ["npm test"]),
    ("npm test > out.txt", ["npm test"], "exact", ["npm test"]),
    ("  npm test  ", ["npm test"], "exact", ["npm test"]),
])
def test_filter_rules_by_contents(command, contents, mode, matched):
    assert _match(command, contents, mode) == matched


def test_filter_rules_strip_all_env_vars_and_skip_compound():
    assert _match("PATH=/x rm -rf y", ["rm -rf y"], "exact") == []
    assert _match("PATH=/x rm -rf y", ["rm -rf y"], "exact", strip_all_env_vars=True) == ["rm -rf y"]
    assert _match("A=1 timeout 5 rm y", ["rm y"], "exact", strip_all_env_vars=True) == ["rm y"]
    assert _match("npm x && rm y", ["npm:*"], "prefix", skip_compound_check=True) == ["npm:*"]
    assert _match("", ["x"], "exact", strip_all_env_vars=True) == []


def test_matching_rules_for_input():
    c = ctx(allow=["npm:*"], deny=["rm:*"], ask=["git push:*"])
    deny, ask, allow = matching_rules_for_input("PATH=/x rm y", c, "prefix")
    assert [r.rule_content for r in deny] == ["rm:*"] and ask == [] and allow == []
    deny, ask, allow = matching_rules_for_input("git push origin", c, "prefix")
    assert [r.rule_content for r in ask] == ["git push:*"]
    deny, ask, allow = matching_rules_for_input("PATH=/x npm i", c, "prefix")
    assert allow == []                                    # allow rules strip only safe env vars
    assert [r.rule_content for r in matching_rules_for_input("npm i", c, "prefix")[2]] == ["npm:*"]


def test_exact_match_permission():
    c = ctx(allow=["ls -la"], deny=["rm x"], ask=["git push"])
    deny = bash_tool_check_exact_match_permission("rm x", c)
    assert deny.behavior == "deny"
    assert deny.message == "Permission to use Bash with command rm x has been denied."
    assert deny.decision_reason == {"type": "rule", "rule": c.always_deny_rules[0]}
    ask = bash_tool_check_exact_match_permission("git push", c)
    assert (ask.behavior, ask.decision_reason) == ("ask", {"type": "rule", "rule": c.always_ask_rules[0]})
    allow = bash_tool_check_exact_match_permission("ls -la", c)
    assert (allow.behavior, allow.updated_input) == ("allow", {"command": "ls -la"})
    other = bash_tool_check_exact_match_permission("pwd", c)
    assert (other.behavior, other.decision_reason) == (
        "passthrough", {"type": "other", "reason": "This command requires approval"})
    assert other.message == "This command requires approval"


# ── mode ─────────────────────────────────────────────────────────────────────

def test_check_permission_mode():
    edits = ctx(mode=PermissionMode.ACCEPT_EDITS)
    res = check_permission_mode("ls && mkdir x", edits)
    assert (res.behavior, res.updated_input) == ("allow", {"command": "mkdir x"})
    assert res.decision_reason == {"type": "mode", "mode": PermissionMode.ACCEPT_EDITS}
    assert check_permission_mode("pip install", edits).message == "No mode-specific validation required"
    assert check_permission_mode("mkdir x", ctx()).behavior == "passthrough"
    assert check_permission_mode("mkdir x", ctx(mode=PermissionMode.BYPASS_PERMISSIONS)).message == (
        "Bypass mode is handled in main permission flow")
    assert check_permission_mode("mkdir x", ctx(mode=PermissionMode.DONT_ASK)).message == (
        "DontAsk mode is handled in main permission flow")
    assert check_permission_mode("", edits).behavior == "passthrough"


# ── output redirections and path constraints ─────────────────────────────────

def test_validate_output_redirections(cwd):
    edits = ctx(mode=PermissionMode.ACCEPT_EDITS)
    ok = validate_output_redirections((OutputRedirection("out", ">"),), cwd, edits)
    assert (ok.behavior, ok.message) == ("passthrough", "No unsafe redirections found")
    assert validate_output_redirections((OutputRedirection("/dev/null", ">"),), cwd, ctx()).behavior == "passthrough"
    blocked = validate_output_redirections((OutputRedirection("/etc/x", ">"),), cwd, edits)
    assert blocked.behavior == "ask"
    assert blocked.message == (
        f"Output redirection to '/etc/x' was blocked. For security, TDDAgents may only write to files in the "
        f"allowed working directories for this session: '{cwd}'.")
    glob = validate_output_redirections((OutputRedirection("*.txt", ">"),), cwd, edits)
    assert glob.message == "Glob patterns are not allowed in write operations. Please specify an exact file path."
    cd = validate_output_redirections((OutputRedirection("out", ">"),), cwd, edits, compound_command_has_cd=True)
    assert cd.behavior == "ask"
    assert cd.decision_reason == {
        "type": "other",
        "reason": "Compound command contains cd with output redirection - manual approval required to prevent "
                  "path resolution bypass"}
    assert validate_output_redirections((), cwd, edits, compound_command_has_cd=True).behavior == "passthrough"
    deny_ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, always_deny_rules=(
        rule(os.path.join(cwd, "s"), PermissionBehavior.DENY),))
    denied = validate_output_redirections((OutputRedirection("s", ">"),), cwd, deny_ctx)
    assert (denied.behavior, denied.message) == (
        "deny", f"Output redirection to '{os.path.join(cwd, 's')}' was blocked by a deny rule.")


def test_check_path_constraints(cwd):
    edits = ctx(mode=PermissionMode.ACCEPT_EDITS)
    psub = check_path_constraints("echo x > >(tee f)", cwd, edits)
    assert psub.behavior == "ask"
    assert psub.decision_reason == {"type": "other", "reason": "Process substitution requires manual approval"}
    assert check_path_constraints("diff <(ls) f", cwd, edits).behavior == "ask"
    exp = check_path_constraints("echo x > $F", cwd, edits)
    assert (exp.behavior, exp.message) == ("ask", "Shell expansion syntax in paths requires manual approval")
    assert check_path_constraints("echo x > /etc/f", cwd, edits).behavior == "ask"
    assert check_path_constraints("cat /etc/passwd", cwd, edits).message.startswith("cat in '/etc/passwd'")
    ok = check_path_constraints("cat a && echo x > b", cwd, edits)
    assert (ok.behavior, ok.message) == ("passthrough", "All path commands validated successfully")


# ── per-subcommand ───────────────────────────────────────────────────────────

def test_bash_tool_check_permission_order(cwd):
    assert bash_tool_check_permission("rm x", ctx(deny=["rm x"]), cwd).behavior == "deny"
    assert bash_tool_check_permission("rm -rf y", ctx(deny=["rm:*"]), cwd).behavior == "deny"
    assert bash_tool_check_permission("git push o", ctx(ask=["git push:*"]), cwd).behavior == "ask"
    # path constraints before allow rules
    assert bash_tool_check_permission("cat /etc/x", ctx(allow=["cat:*"]), cwd).behavior == "ask"
    assert bash_tool_check_permission("pytest -q", ctx(allow=["pytest -q"]), cwd).behavior == "allow"
    allowed = bash_tool_check_permission("pytest -k x", ctx(allow=["pytest:*"]), cwd)
    assert allowed.decision_reason is not None and allowed.decision_reason["type"] == "rule"
    # sed constraints after allow rules, before mode
    sed = bash_tool_check_permission("sed 's/a/b/w o'", ctx(), cwd)
    assert sed.message == "sed command requires approval (contains potentially dangerous operations)"
    assert bash_tool_check_permission("mkdir x", ctx(mode=PermissionMode.ACCEPT_EDITS), cwd).behavior == "allow"
    ro = bash_tool_check_permission("ls", ctx(), cwd)
    assert ro.decision_reason == {"type": "other", "reason": "Read-only command is allowed"}
    assert bash_tool_check_permission("pip install x", ctx(), cwd).behavior == "passthrough"


def test_check_command_and_suggest_rules(cwd):
    assert check_command_and_suggest_rules("rm x", ctx(deny=["rm x"]), cwd).behavior == "deny"
    inj = check_command_and_suggest_rules("echo $(id)", ctx(), cwd)
    assert (inj.behavior, inj.message) == ("ask", "Command contains $() command substitution")
    assert check_command_and_suggest_rules("pip install x", ctx(), cwd).behavior == "passthrough"
    assert check_command_and_suggest_rules("cat /etc/x", ctx(), cwd).behavior == "ask"


def test_injection_check_can_be_disabled(cwd, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", "1")
    assert check_command_and_suggest_rules("echo $(id)", ctx(), cwd).behavior == "passthrough"


# ── command identity ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("command, is_git", [
    ("git status", True), ("git", True), ("timeout 5 git log", True), ("'git' status", True),
    ("xargs git log", True), ("xargs -0 git", True), ("gitk", False), ("ls", False), ("echo ${}", False),
    ("NODE_ENV=x git log", True),
])
def test_is_normalized_git_command(command, is_git):
    assert is_normalized_git_command(command) is is_git


@pytest.mark.parametrize("command, is_cd", [
    ("cd x", True), ("pushd x", True), ("popd", True), ("'cd' x", True), ("cdx", False), ("ls", False),
    ("cd ${}", True), ("echo ${}", False),
])
def test_is_normalized_cd_command(command, is_cd):
    assert is_normalized_cd_command(command) is is_cd


def test_command_has_any_cd():
    assert command_has_any_cd("ls && cd x") is True
    assert command_has_any_cd("ls && pwd") is False


# ── pipes ────────────────────────────────────────────────────────────────────

def test_check_command_operator_permissions(cwd):
    unsafe = check_command_operator_permissions("(rm x)", ctx(), cwd)
    assert (unsafe.behavior, unsafe.message) == ("ask", "This command uses shell operators that require approval "
                                                        "for safety")
    nopipe = check_command_operator_permissions("ls", ctx(), cwd)
    assert (nopipe.behavior, nopipe.message) == ("passthrough", "No pipes found in command")
    allowed = check_command_operator_permissions("ls | wc -l", ctx(), cwd)
    assert allowed.behavior == "allow" and allowed.updated_input == {"command": "ls | wc -l"}
    mixed = check_command_operator_permissions("ls | pip install x", ctx(), cwd)
    assert mixed.behavior == "ask"
    assert mixed.message == ("This Bash command contains multiple operations. The following part requires "
                             "approval: pip install x")
    denied = check_command_operator_permissions("ls | rm y", ctx(deny=["rm y"]), cwd)
    assert denied.behavior == "deny"
    assert denied.message == "Permission to use Bash with command rm y has been denied."
    cds = check_command_operator_permissions("cd a | cd b", ctx(), cwd)
    assert cds.message == "Multiple directory changes in one command require approval for clarity"
    cdgit = check_command_operator_permissions("cd a | git log", ctx(), cwd)
    assert cdgit.message == "Compound commands with cd and git require approval to prevent bare repository attacks"
    assert check_command_operator_permissions("ls | wc > out", ctx(mode=PermissionMode.ACCEPT_EDITS),
                                              cwd).behavior == "allow"


def test_operator_injection_reason(cwd, monkeypatch):
    res = check_command_operator_permissions("(echo $(id))", ctx(), cwd)
    assert res.message == "Command contains $() command substitution"


# ── bash_tool_has_permission ─────────────────────────────────────────────────

@pytest.mark.parametrize("command, behavior", [
    ("ls -la", "allow"),
    ("git status", "allow"),
    ("cat a.txt && wc -l a.txt", "allow"),
    ("pip install x", "passthrough"),
    ("ls && pip install x", "passthrough"),
    ("rm -rf /", "ask"),
    ("cat /etc/passwd", "ask"),
    ("echo $(id)", "ask"),
    ("cd a && cd b", "ask"),
    ("cd a && git status", "ask"),
    ("echo ${}", "ask"),
    ("ls | grep x", "allow"),
])
def test_bash_tool_has_permission(cwd, command, behavior):
    assert bash_tool_has_permission(command, ctx(), cwd).behavior == behavior


def test_has_permission_messages_and_rules(cwd):
    bad = bash_tool_has_permission("echo ${}", ctx(), cwd)
    assert bad.message == "Command contains malformed syntax that cannot be parsed: Bad substitution: ${}"
    assert bash_tool_has_permission("rm x", ctx(deny=["rm x"]), cwd).behavior == "deny"
    compound_deny = bash_tool_has_permission("ls && rm y", ctx(deny=["rm y"]), cwd)
    assert compound_deny.behavior == "deny"
    assert compound_deny.message == "Permission to use Bash with command ls && rm y has been denied."
    assert bash_tool_has_permission("pytest -q", ctx(allow=["pytest:*"]), cwd).behavior == "allow"
    both = bash_tool_has_permission("pytest -q && pip install x", ctx(allow=["pytest:*", "pip install:*"]), cwd)
    assert both.behavior == "allow"
    one = bash_tool_has_permission("pytest -q && pip install x", ctx(allow=["pytest:*"]), cwd)
    assert one.behavior == "passthrough"
    assert one.message == ("This Bash command contains multiple operations. The following part requires approval: "
                           "pip install x")
    assert bash_tool_has_permission(f"cd {cwd} && ls", ctx(), cwd).behavior == "allow"   # cd to cwd is dropped
    # operators are checked before the misparsing gate, as upstream: $() is an unsafe compound
    assert bash_tool_has_permission("echo $(id)", ctx(allow=["echo $(id)"]), cwd).behavior == "ask"
    exact = bash_tool_has_permission("echo `id`", ctx(allow=["echo `id`"]), cwd)
    assert exact.behavior == "allow"                    # exact rule rescues a misparsing ask
    assert bash_tool_has_permission("echo `id`", ctx(), cwd).message == (
        "Command contains backticks (`) for command substitution")
    ask_one = bash_tool_has_permission("ls && cat /etc/x", ctx(), cwd)
    assert ask_one.behavior == "ask" and ask_one.message.startswith("cat in '/etc/x'")
    redirect = bash_tool_has_permission("ls > /etc/out", ctx(mode=PermissionMode.ACCEPT_EDITS), cwd)
    assert redirect.behavior == "ask" and redirect.message.startswith("Output redirection to '/etc/out'")


def test_has_permission_heredoc_misparsing_retry(cwd):
    cmd = "git commit -m \"$(cat <<'EOF'\nmsg\nEOF\n)\""
    assert bash_tool_has_permission(cmd, ctx(), cwd).behavior in ("passthrough", "ask")


def test_has_permission_pipe_with_injection_is_asked(cwd):
    res = bash_tool_has_permission("ls | wc -c $(id)", ctx(), cwd)
    assert res.behavior == "ask"


def test_module_reexports_path_helpers():
    from app.loop.permissions import bash_path_validation as pv
    assert bp.format_directory_list is pv.format_directory_list
    assert bp.MAX_DIRS_TO_LIST == 5
