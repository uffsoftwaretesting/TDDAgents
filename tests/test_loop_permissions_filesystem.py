"""
Tests for filesystem permissions (Phase 3): upstream's `matchingRuleForInput`,
`checkPathSafetyForAutoEdit`, `pathInWorkingPath`, `isPathAllowed` and
`checkWritePermissionForTool`, plus the file tools' `check_permissions` wiring.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.loop.context import AppState, AppStateStore, tool_context_for
from app.loop.permissions.filesystem import (
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    FILE_EDIT_TOOL_NAME,
    FILE_READ_TOOL_NAME,
    PathCheckResult,
    all_working_directories,
    check_file_write_permissions,
    check_path_safety_for_auto_edit,
    check_write_permission_for_tool,
    expand_path,
    get_paths_for_permission_check,
    get_patterns_by_root,
    has_suspicious_windows_path_pattern,
    is_dangerous_file_path_to_auto_edit,
    is_path_allowed,
    matching_rule_for_input,
    normalize_case_for_comparison,
    path_from_tool_input,
    path_in_allowed_working_path,
    path_in_working_path,
    pattern_with_root,
    permission_cwd,
    root_path_for_source,
    workspace_absolute_path,
)
from app.loop.permissions.types import (
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionRuleSource,
    ToolPermissionContext,
)

ALLOW, DENY, ASK = PermissionBehavior.ALLOW, PermissionBehavior.DENY, PermissionBehavior.ASK
PROJECT = PermissionRuleSource.PROJECT_SETTINGS
USER = PermissionRuleSource.USER_SETTINGS
FLAG = PermissionRuleSource.FLAG_SETTINGS
CWD = "/work/ws"


def edit_rule(content, behavior=DENY, source=PROJECT, tool=FILE_EDIT_TOOL_NAME):
    return PermissionRule(tool_name=tool, rule_behavior=behavior, rule_content=content, source=source)


def ctx_with(*rules, mode=PermissionMode.DEFAULT, **kwargs):
    return ToolPermissionContext(
        mode=mode,
        always_allow_rules=tuple(r for r in rules if r.rule_behavior == ALLOW),
        always_deny_rules=tuple(r for r in rules if r.rule_behavior == DENY),
        always_ask_rules=tuple(r for r in rules if r.rule_behavior == ASK),
        **kwargs,
    )


# ── dangerous paths and suspicious patterns ──────────────────────────────────

def test_constants_are_upstreams_plus_the_product_rename():
    assert DANGEROUS_FILES == (
        ".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile", ".profile",
        ".ripgreprc", ".mcp.json", ".claude.json", ".tddagents.json")
    assert DANGEROUS_DIRECTORIES == (".git", ".vscode", ".idea", ".claude", ".tddagents")
    assert (FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME) == ("Edit", "Read")


@pytest.mark.parametrize("name", DANGEROUS_FILES)
def test_every_dangerous_file_is_detected_case_insensitively(name):
    assert is_dangerous_file_path_to_auto_edit(f"/w/{name}")
    assert is_dangerous_file_path_to_auto_edit(f"/w/sub/{name.upper()}")


@pytest.mark.parametrize("name", DANGEROUS_DIRECTORIES)
def test_every_dangerous_directory_is_detected_at_any_depth(name):
    assert is_dangerous_file_path_to_auto_edit(f"/w/{name}/f")
    assert is_dangerous_file_path_to_auto_edit(f"{name.upper()}/a/b")


def test_ordinary_paths_and_unc_paths():
    assert not is_dangerous_file_path_to_auto_edit("/w/src/main.py")
    assert not is_dangerous_file_path_to_auto_edit("/w/gitconfig")
    assert is_dangerous_file_path_to_auto_edit("//server/share/x")
    assert is_dangerous_file_path_to_auto_edit("\\\\server\\share")


def test_claude_worktrees_is_structural_but_nested_claude_is_not():
    assert not is_dangerous_file_path_to_auto_edit("/w/.claude/worktrees/x/a.py")
    assert not is_dangerous_file_path_to_auto_edit("/w/.CLAUDE/WORKTREES/x")
    assert is_dangerous_file_path_to_auto_edit("/w/.claude/worktrees/x/.claude/settings.json")
    assert is_dangerous_file_path_to_auto_edit("/w/.claude")
    assert is_dangerous_file_path_to_auto_edit("/w/.tddagents/worktrees/x")


@pytest.mark.parametrize("path", [
    "/w/GIT~1/config", "\\\\?\\C:\\x", "\\\\.\\C:\\x", "//?/C:/x", "//./C:/x",
    "/w/.git.", "/w/a ", "/w/settings.json.PRN", "/w/x.com1", "/w/.../f", "...", "/w/a/...",
])
def test_suspicious_windows_patterns(path):
    assert has_suspicious_windows_path_pattern(path)


@pytest.mark.parametrize("path", ["/w/a.py", "/w/[...]name.tsx", "/w/a..b", "/w/a~b", "/w/CONFIG.py"])
def test_ordinary_paths_are_not_suspicious(path):
    assert not has_suspicious_windows_path_pattern(path)


def test_safety_check_messages_and_order():
    assert check_path_safety_for_auto_edit("/w/a.py", ("/w/a.py",)) == (True, "")
    assert check_path_safety_for_auto_edit("/w/x~1", ("/w/x~1",)) == (False, (
        "The agent requested permissions to write to /w/x~1, which contains a suspicious Windows path pattern "
        "that requires manual approval."))
    assert check_path_safety_for_auto_edit("/w/.git/x", ("/w/.git/x",)) == (
        False, "The agent requested permissions to edit /w/.git/x which is a sensitive file.")
    # Every candidate is checked, so a resolved form pointing somewhere dangerous counts.
    assert check_path_safety_for_auto_edit("/w/link", ("/w/link", "/home/u/.bashrc"))[0] is False
    assert check_path_safety_for_auto_edit("/w/link", ("/w/link", "/w/x~2"))[0] is False


def test_normalize_case_and_expand_path():
    assert normalize_case_for_comparison("Foo/BAR") == "foo/bar"
    assert expand_path("a/../b", "/c") == "/c/b"
    assert expand_path("/abs/./x", "/c") == "/abs/x"
    assert expand_path("~", "/c") == os.path.expanduser("~")
    assert expand_path("~/f", "/c") == os.path.join(os.path.expanduser("~"), "f")


def test_paths_for_permission_check(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    assert get_paths_for_permission_check(str(real)) == (str(real),)
    assert get_paths_for_permission_check(str(link)) == (str(link), str(real))
    assert get_paths_for_permission_check("//server/share") == ("//server/share",)
    assert get_paths_for_permission_check("\\\\server\\share") == ("\\\\server\\share",)


# ── working directories ──────────────────────────────────────────────────────

def test_path_in_working_path():
    assert path_in_working_path("/w/ws", "/w/ws")
    assert path_in_working_path("/w/ws/a/b", "/w/ws")
    assert path_in_working_path("/W/WS/A", "/w/ws")
    assert path_in_working_path("/private/tmp/x", "/tmp")
    assert path_in_working_path("/private/var/x", "/var")
    assert not path_in_working_path("/w/ws2/a", "/w/ws")
    assert not path_in_working_path("/w", "/w/ws")
    assert not path_in_working_path("/w/ws/../x", "/w/ws")
    assert not path_in_working_path("/w/..ws", "/w/ws")


def test_path_in_allowed_working_path_needs_every_candidate_inside():
    ctx = ToolPermissionContext(additional_working_directories=("/extra",))
    assert all_working_directories(ctx, "/w") == ("/w", "/extra")
    assert path_in_allowed_working_path("/extra/a", ctx, "/w", ("/extra/a",))
    assert path_in_allowed_working_path("/w/a", ctx, "/w", ("/w/a",))
    assert not path_in_allowed_working_path("/w/a", ctx, "/w", ("/w/a", "/etc/passwd"))
    assert not path_in_allowed_working_path("/other", ctx, "/w", ("/other",))


def test_path_in_allowed_working_path_resolves_symlinks(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (ws / "link").symlink_to(outside)
    ctx = ToolPermissionContext()
    assert path_in_allowed_working_path(str(ws / "file"), ctx, str(ws))
    assert not path_in_allowed_working_path(str(ws / "link"), ctx, str(ws))


# ── path rules ───────────────────────────────────────────────────────────────

def test_pattern_with_root():
    ctx = ToolPermissionContext(settings_roots={USER: "/home/u/.tddagents"})
    assert pattern_with_root("//etc/x", PROJECT, ctx, CWD) == ("/etc/x", "/")
    assert pattern_with_root("~/notes", PROJECT, ctx, CWD) == ("/notes", os.path.expanduser("~"))
    assert pattern_with_root("/src/**", PROJECT, ctx, CWD) == ("/src/**", CWD)
    assert pattern_with_root("/src/**", USER, ctx, CWD) == ("/src/**", "/home/u/.tddagents")
    assert pattern_with_root("./.env", PROJECT, ctx, CWD) == (".env", None)
    assert pattern_with_root("*.py", PROJECT, ctx, CWD) == ("*.py", None)
    assert root_path_for_source(FLAG, ctx, CWD) == CWD


def test_patterns_by_root_take_only_content_rules_on_the_right_tool():
    a, b = edit_rule("src/**"), edit_rule("/lib")
    ctx = ctx_with(a, b, edit_rule(None), edit_rule("x", tool="Bash"), edit_rule("r", tool=FILE_READ_TOOL_NAME))
    assert get_patterns_by_root(ctx, "edit", "deny", CWD) == {None: {"src/**": a}, CWD: {"/lib": b}}
    read_rule = ctx.always_deny_rules[-1]
    assert get_patterns_by_root(ctx, "read", "deny", CWD) == {None: {"r": read_rule}}
    assert get_patterns_by_root(ctx, "edit", "allow", CWD) == {}


@pytest.mark.parametrize("pattern,path,expected", [
    ("src/**", f"{CWD}/src/a.py", True),
    ("src/**", f"{CWD}/src", True),  # `ignore` matches the directory itself
    ("src", f"{CWD}/src/deep/a.py", True),
    ("*.py", f"{CWD}/a/b.py", True),
    ("*.py", f"{CWD}/a/b.txt", False),
    ("/src/**", f"{CWD}/src/a.py", True),
    ("/src/**", f"{CWD}/x/src/a.py", False),
    ("//etc/**", "/etc/passwd", True),
    ("tests/*", f"{CWD}/tests/test_a.py", True),
    ("src/**", "/elsewhere/src/a.py", False),
])
def test_matching_rule_for_input(pattern, path, expected):
    rule = edit_rule(pattern)
    found = matching_rule_for_input(path, ctx_with(rule), "edit", "deny", CWD)
    assert (found is rule) is expected


def test_matching_rule_uses_the_settings_root_for_slash_patterns():
    rule = edit_rule("/secrets/**", source=USER)
    ctx = ctx_with(rule, settings_roots={USER: "/home/u/.tddagents"})
    assert matching_rule_for_input("/home/u/.tddagents/secrets/k", ctx, "edit", "deny", CWD) is rule
    assert matching_rule_for_input(f"{CWD}/secrets/k", ctx, "edit", "deny", CWD) is None


def test_matching_rule_negation_last_match_wins_and_root_itself_never_matches():
    deny_all = edit_rule("src/**")
    keep = edit_rule("!src/keep.py")
    ctx = ctx_with(deny_all, keep)
    assert matching_rule_for_input(f"{CWD}/src/a.py", ctx, "edit", "deny", CWD) is deny_all
    assert matching_rule_for_input(f"{CWD}/src/keep.py", ctx, "edit", "deny", CWD) is None
    assert matching_rule_for_input(CWD, ctx_with(edit_rule("*")), "edit", "deny", CWD) is None


def test_matching_rule_returns_the_original_wildcard_rule():
    star = edit_rule("build/**")
    plain = edit_rule("build")
    assert matching_rule_for_input(f"{CWD}/build/x", ctx_with(star), "edit", "deny", CWD) is star
    assert matching_rule_for_input(f"{CWD}/build/x", ctx_with(plain), "edit", "deny", CWD) is plain


def test_matching_rule_by_behavior_and_relative_input():
    allow, ask = edit_rule("a/**", ALLOW), edit_rule("b/**", ASK)
    ctx = ctx_with(allow, ask)
    assert matching_rule_for_input("a/x", ctx, "edit", "allow", CWD) is allow
    assert matching_rule_for_input("b/x", ctx, "edit", "ask", CWD) is ask
    assert matching_rule_for_input("a/x", ctx, "edit", "ask", CWD) is None


# ── isPathAllowed ────────────────────────────────────────────────────────────

def test_is_path_allowed_deny_rules_come_first():
    deny = edit_rule("secret", tool=FILE_READ_TOOL_NAME)
    ctx = ctx_with(deny, mode=PermissionMode.ACCEPT_EDITS)
    assert is_path_allowed(f"{CWD}/secret", ctx, CWD, "read") == PathCheckResult(False, {"type": "rule", "rule": deny})
    write_deny = edit_rule("secret")
    assert is_path_allowed(f"{CWD}/secret", ctx_with(write_deny), CWD, "create") == PathCheckResult(
        False, {"type": "rule", "rule": write_deny})


def test_is_path_allowed_safety_check_on_writes_only():
    ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    res = is_path_allowed(f"{CWD}/.git/config", ctx, CWD, "write")
    assert res == PathCheckResult(False, {
        "type": "safetyCheck",
        "reason": f"The agent requested permissions to edit {CWD}/.git/config which is a sensitive file."})
    assert is_path_allowed(f"{CWD}/.git/config", ctx, CWD, "read") == PathCheckResult(True)


def test_is_path_allowed_working_dir_reads_and_accept_edits_writes():
    default = ToolPermissionContext()
    assert is_path_allowed(f"{CWD}/a", default, CWD, "read") == PathCheckResult(True)
    assert is_path_allowed(f"{CWD}/a", default, CWD, "write") == PathCheckResult(False)
    edits = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert is_path_allowed(f"{CWD}/a", edits, CWD, "create") == PathCheckResult(True)
    assert is_path_allowed("/etc/x", edits, CWD, "write") == PathCheckResult(False)
    assert is_path_allowed("/etc/x", default, CWD, "read") == PathCheckResult(False)


def test_is_path_allowed_falls_back_to_allow_rules():
    allow = edit_rule("//etc/x", ALLOW)
    assert is_path_allowed("/etc/x", ctx_with(allow), CWD, "write") == PathCheckResult(
        True, {"type": "rule", "rule": allow})
    read_allow = edit_rule("//etc/**", ALLOW, tool=FILE_READ_TOOL_NAME)
    assert is_path_allowed("/etc/y", ctx_with(read_allow), CWD, "read") == PathCheckResult(
        True, {"type": "rule", "rule": read_allow})


# ── checkWritePermissionForTool ──────────────────────────────────────────────

def test_write_deny_rule():
    deny = edit_rule("src/**")
    res = check_write_permission_for_tool(f"{CWD}/src/a.py", {}, ctx_with(deny), CWD)
    assert (res.behavior, res.message, res.decision_reason) == (
        DENY, f"Permission to edit {CWD}/src/a.py has been denied.", {"type": "rule", "rule": deny})


def test_write_deny_rule_beats_bypass_and_accept_edits():
    deny = edit_rule("src/**")
    ctx = ctx_with(deny, mode=PermissionMode.ACCEPT_EDITS)
    assert check_write_permission_for_tool(f"{CWD}/src/a.py", {}, ctx, CWD).behavior == DENY


def test_write_safety_check_asks_before_ask_and_allow_rules():
    allow = edit_rule(".git/**", ALLOW)
    ctx = ctx_with(allow, mode=PermissionMode.ACCEPT_EDITS)
    res = check_write_permission_for_tool(f"{CWD}/.git/config", {}, ctx, CWD)
    message = f"The agent requested permissions to edit {CWD}/.git/config which is a sensitive file."
    assert (res.behavior, res.message, res.decision_reason) == (
        ASK, message, {"type": "safetyCheck", "reason": message})


def test_write_ask_rule_beats_accept_edits():
    ask = edit_rule("docs/**", ASK)
    res = check_write_permission_for_tool(f"{CWD}/docs/a.md", {}, ctx_with(ask, mode=PermissionMode.ACCEPT_EDITS), CWD)
    assert (res.behavior, res.message, res.decision_reason) == (
        ASK, f"The agent requested permissions to write to {CWD}/docs/a.md, but you haven't granted it yet.",
        {"type": "rule", "rule": ask})


def test_write_accept_edits_allows_inside_the_working_dir_only():
    args = {"file_path": "a.py"}
    ctx = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    res = check_write_permission_for_tool(f"{CWD}/a.py", args, ctx, CWD)
    assert (res.behavior, res.updated_input, res.decision_reason) == (
        ALLOW, args, {"type": "mode", "mode": PermissionMode.ACCEPT_EDITS})
    outside = check_write_permission_for_tool("/etc/x", args, ctx, CWD)
    assert (outside.behavior, outside.decision_reason) == (
        ASK, {"type": "workingDir", "reason": "Path is outside allowed working directories"})


def test_write_allow_rule_allows_outside_accept_edits():
    allow = edit_rule("out/**", ALLOW)
    args = {"file_path": "out/a"}
    res = check_write_permission_for_tool(f"{CWD}/out/a", args, ctx_with(allow), CWD)
    assert (res.behavior, res.updated_input, res.decision_reason) == (
        ALLOW, args, {"type": "rule", "rule": allow})


def test_write_default_mode_asks_with_no_reason_inside_the_working_dir():
    res = check_write_permission_for_tool(f"{CWD}/a.py", {}, ToolPermissionContext(), CWD)
    assert (res.behavior, res.message, res.decision_reason) == (
        ASK, f"The agent requested permissions to write to {CWD}/a.py, but you haven't granted it yet.", None)


def test_write_rules_check_the_resolved_path(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "alias").symlink_to(ws / "src")
    deny = edit_rule("src/**")
    res = check_write_permission_for_tool(str(ws / "alias" / "a.py"), {}, ctx_with(deny), str(ws))
    assert res.behavior == DENY
    ask = edit_rule("src/**", ASK)
    edits = ctx_with(ask, mode=PermissionMode.ACCEPT_EDITS)
    asked = check_write_permission_for_tool(str(ws / "alias" / "a.py"), {}, edits, str(ws))
    assert (asked.behavior, asked.decision_reason) == (ASK, {"type": "rule", "rule": ask})


# ── the file tools' wiring ───────────────────────────────────────────────────

def test_workspace_absolute_path_and_tool_input():
    assert workspace_absolute_path("a/b.py", "/ws") == "/ws/a/b.py"
    assert workspace_absolute_path("/a.py", "/ws") == "/ws/a.py"
    assert workspace_absolute_path("..\\x", "/ws") == "/x"
    assert path_from_tool_input({"file_path": "f", "path": "p"}) == "f"
    assert path_from_tool_input({"path": "p"}) == "p"
    assert path_from_tool_input({}) == ""


def test_permission_cwd_prefers_the_workspace_root():
    assert permission_cwd(SimpleNamespace(workspace=SimpleNamespace(root="/ws"))) == "/ws"  # type: ignore[arg-type]
    assert permission_cwd(SimpleNamespace(workspace=None)) == os.getcwd()  # type: ignore[arg-type]
    assert permission_cwd(SimpleNamespace()) == os.getcwd()  # type: ignore[arg-type]


def test_check_file_write_permissions_reads_the_app_state_context(tmp_path):
    perm = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS, always_deny_rules=(edit_rule("locked/**"),))
    ctx = tool_context_for(AppStateStore(state=AppState(tool_permission_context=perm)),
                           workspace=SimpleNamespace(root=str(tmp_path)))
    check = check_file_write_permissions()
    allowed = check({"file_path": "src/a.py"}, ctx)
    assert (allowed.behavior, allowed.updated_input) == (ALLOW, {"file_path": "src/a.py"})
    assert check({"file_path": "locked/a.py"}, ctx).message == (
        f"Permission to edit {tmp_path}/locked/a.py has been denied.")
    assert check({"file_path": "../escape.py"}, ctx).decision_reason == {
        "type": "workingDir", "reason": "Path is outside allowed working directories"}


def test_fs_tools_carry_the_write_check():
    from app.loop.tools.fs import build_edit_tool, build_write_file_tool

    ctx = tool_context_for(AppStateStore(), permission_context=ToolPermissionContext(),
                           workspace=SimpleNamespace(root="/ws"))
    for build in (build_write_file_tool, build_edit_tool):
        res = asyncio.run(build().check_permissions({"file_path": "a.py"}, ctx))
        assert res.behavior == ASK
        assert res.message == "The agent requested permissions to write to /ws/a.py, but you haven't granted it yet."
