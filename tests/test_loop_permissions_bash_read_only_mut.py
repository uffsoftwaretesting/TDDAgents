"""
Mutation-driven pins for the Bash read-only classifier (`bash_read_only.py`,
`bash_read_only_commands.py`), the obfuscated-flag quote chain and quoted-newline checks
(`bash_security.py`), and the Bash tool helpers (`tools/bash.py`).

Every expectation follows claude-code v2.1.88 (`readOnlyValidation.ts`,
`readOnlyCommandValidation.ts`, `bashSecurity.ts`, `BashTool.tsx`); each test targets a
mutant the earlier suite let survive.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.loop.permissions import bash_read_only as ro
from app.loop.permissions import bash_read_only_commands as roc
from app.loop.permissions import bash_security as bs
from app.loop.permissions.bash_read_only_commands import CommandConfig, validate_flags
from app.loop.tools import bash as bash_mod

safe = ro.is_command_safe_via_flag_parsing


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(roc, "get_platform", lambda: "windows")


# ── git tag callback ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--no-contains", "--merged", "--no-merged", "--sort"])
def test_git_tag_flags_with_args_consume_their_value(flag):
    assert roc._cb_git_tag("", [flag, "v1"]) is False


def test_git_tag_empty_token_handling():
    # the empty token is skipped, never a reset of the scan
    assert roc._cb_git_tag("", ["--sort", "x", ""]) is False
    # an empty token does not end the scan
    assert roc._cb_git_tag("", ["", "v1"]) is True


def test_git_tag_double_dash_advances():
    assert roc._cb_git_tag("", ["--sort", "x", "--"]) is False


def test_git_tag_bundled_list_flag():
    assert roc._cb_git_tag("", ["-lv", "v1"]) is False


def test_git_tag_inline_value_advances_one():
    assert roc._cb_git_tag("", ["--sort", "x", "--format=y"]) is False


def test_git_tag_flag_with_arg_after_another_flag():
    assert roc._cb_git_tag("", ["-v", "--sort", "b"]) is False


# ── git branch callback ──────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--contains", "--no-contains", "--sort"])
def test_git_branch_flags_with_args_consume_their_value(flag):
    assert roc._cb_git_branch("", [flag, "x"]) is False


def test_git_branch_empty_tokens():
    assert roc._cb_git_branch("", ["--sort", "x", ""]) is False
    assert roc._cb_git_branch("", ["", "x"]) is True


def test_git_branch_double_dash_makes_flags_positional():
    assert roc._cb_git_branch("", ["--", "-l"]) is True
    assert roc._cb_git_branch("", ["--sort", "x", "--"]) is False


def test_git_branch_bundled_list_flag():
    assert roc._cb_git_branch("", ["-lv", "x"]) is False


def test_git_branch_inline_optional_value():
    assert roc._cb_git_branch("", ["--merged=x", "y"]) is False


def test_git_branch_inline_value_advances_one():
    # an inline value advances one token; it never rewinds the scan
    assert roc._cb_git_branch("", ["--sort", "x", "--format=y"]) is False


def test_git_branch_flag_with_arg_after_another_flag():
    assert roc._cb_git_branch("", ["-v", "--sort", "x"]) is False


def test_git_branch_positional_then_flag_then_positional():
    assert roc._cb_git_branch("", ["--merged", "a", "-x", "b"]) is True


def test_git_remote_show_accepts_uppercase_names():
    assert roc._cb_git_remote_show("", ["ORIGIN"]) is False


# ── gh callback ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("args, dangerous", [
    (["", "https://x"], True),
    (["--a/b/c=d"], False),
    (["--x=https://a=b"], True),
    (["--a/b/c"], False),
    (["-R", "https://x"], True),
    (["--a/=b/c"], False),
    (["--x=/a/b"], True),
    (["--x=", "https://a"], True),
    (["plain", "https://x"], True),
])
def test_gh_is_dangerous_callback(args, dangerous):
    assert roc.gh_is_dangerous_callback("", args) is dangerous


# ── ps / date / tput callbacks ───────────────────────────────────────────────

def test_ps_bsd_e_with_uppercase_letters():
    assert roc._cb_ps("", ["Ae"]) is True


@pytest.mark.parametrize("flag", ["--date", "--reference", "--rfc-3339"])
def test_date_flags_with_args(flag):
    assert roc._cb_date("", [flag, "0101"]) is False


@pytest.mark.parametrize("args, dangerous", [
    (["-d", "x", "--date=y"], False),
    (["--date=y", "0101"], True),
    (["+%s", "-d", "x"], False),
    (["-d", "x", "0101"], True),
    (["-u", "0101"], True),
    (["+%s", "0101"], True),
])
def test_date_scan_steps(args, dangerous):
    assert roc._cb_date("", args) is dangerous


@pytest.mark.parametrize("args, dangerous", [
    (["-T", "clear", "--"], False),
    (["-xy", "cols"], False),
    (["--Sx"], False),
    (["-x", "-T", "clear"], False),
    (["-T", "xterm", "clear"], True),
    (["-T", "clear"], False),
    (["-x", "clear"], True),
    (["cols", "clear"], True),
])
def test_tput_scan(args, dangerous):
    assert roc._cb_tput("", args) is dangerous


# ── UNC paths (Windows only) ─────────────────────────────────────────────────

@pytest.mark.parametrize("s", ["x@ssl@443", "x@443@ssl", "davwwwroot", "x DavWWWRoot y"])
def test_unc_case_insensitive_markers(windows, s):
    assert roc.contains_vulnerable_unc_path(s) is True


def test_unc_is_windows_only():
    assert roc.contains_vulnerable_unc_path("\\\\host\\share") is False


def test_unc_checks_reach_read_only_paths(windows):
    assert ro.is_command_read_only("ls \\\\host\\share") is False
    assert ro.check_read_only_constraints("ls", False).behavior == "allow"


# ── validate_flags ───────────────────────────────────────────────────────────

NONE_R = CommandConfig(safe_flags={"-r": "none"})


def test_validate_flags_empty_token_does_not_skip_or_stop():
    assert validate_flags(["c", "", "-x"], 1, CommandConfig(safe_flags={})) is False


def test_validate_flags_xargs_targets_only_for_xargs():
    cfg = CommandConfig(safe_flags={})
    assert validate_flags(["grep", "x"], 1, cfg, command_name="grep", xargs_target_commands=("echo",)) is True
    assert validate_flags(["xargs", "rm"], 1, cfg, command_name="xargs", xargs_target_commands=("echo",)) is False


def test_validate_flags_double_dash_when_not_respected():
    cfg = CommandConfig(safe_flags={}, respects_double_dash=False)
    assert validate_flags(["c", "--", "-x"], 1, cfg) is False


def test_validate_flags_git_numeric_flag_then_unknown():
    assert validate_flags(["git", "-5", "-z"], 1, CommandConfig(safe_flags={}), command_name="git") is False


def test_validate_flags_grep_attached_number_then_unknown():
    cfg = CommandConfig(safe_flags={"-A": "number"})
    assert validate_flags(["grep", "-A3", "-Z"], 1, cfg, command_name="grep") is False


def test_validate_flags_bundled_checks_every_char():
    assert validate_flags(["c", "-Zr"], 1, NONE_R) is False
    assert validate_flags(["c", "-rr", "-Z"], 1, NONE_R) is False


def test_validate_flags_none_flag_then_unknown():
    assert validate_flags(["c", "-r", "-Z"], 1, NONE_R) is False


def test_validate_flags_inline_value_then_unknown():
    assert validate_flags(["c", "--n=3", "-Z"], 1, CommandConfig(safe_flags={"--n": "number"})) is False


def test_validate_flags_separate_value_then_unknown():
    assert validate_flags(["c", "-n", "3", "-Z"], 1, CommandConfig(safe_flags={"-n": "number"})) is False


def test_validate_flags_value_must_not_be_a_flag_even_after_empty_token():
    cfg = CommandConfig(safe_flags={"--sort": "string"})
    assert validate_flags(["", "--sort", "-x"], 1, cfg, command_name="git") is False


def test_validate_flags_git_reverse_sort_uppercase():
    cfg = CommandConfig(safe_flags={"--sort": "string"})
    assert validate_flags(["git", "--sort=-Refname"], 1, cfg, command_name="git") is True


# ── is_command_safe_via_flag_parsing ─────────────────────────────────────────

@pytest.mark.parametrize("command", ["git ls-remote a@b", "git ls-remote a:b"])
def test_ls_remote_rejects_url_like_targets(command):
    assert safe(command) is False


def test_ls_remote_flag_values_are_not_targets():
    assert safe("git ls-remote --sort=version:refname origin") is True


def test_glob_tokens_reach_callbacks_as_their_pattern():
    assert safe("date +%Y*") is True


def test_empty_token_does_not_stop_dollar_and_brace_checks():
    assert safe("grep '' {a,b}") is False


def test_brace_range_is_rejected():
    assert safe("grep x {a..b}") is False


def test_xargs_targets_are_enforced():
    assert safe("xargs rm") is False
    assert safe("xargs grep x") is True


def test_rg_and_grep_reject_newlines():
    assert safe("rg x\ny") is False
    assert safe("grep x\ny") is False


# ── contains_unquoted_expansion ──────────────────────────────────────────────

@pytest.mark.parametrize("command, expected", [
    ("*x", True),
    ("\\a*", True),
    ("$x", True),
    ('"a"*', True),
    ("'$x'", False),
])
def test_contains_unquoted_expansion(command, expected):
    assert ro.contains_unquoted_expansion(command) is expected


# ── is_command_read_only ─────────────────────────────────────────────────────

def test_read_only_strips_js_whitespace_including_bom():
    assert ro.is_command_read_only("﻿pwd") is True
    assert ro.is_command_read_only("pwd﻿ 2>&1") is True
    assert ro.is_command_read_only("pwd 2>&1") is True


def test_git_flag_guard_applies_only_to_git():
    assert ro.is_command_read_only("ls -c x") is True


# ── git internal paths ───────────────────────────────────────────────────────

def test_command_has_any_git_strips_bom():
    assert ro.command_has_any_git("﻿git status") is True


def test_write_paths_keep_variables_literal():
    assert ro.extract_write_paths_from_subcommand("mkdir $X/refs") == ["$X/refs"]


def test_writes_to_git_internal_paths():
    assert ro.command_writes_to_git_internal_paths("﻿mkdir refs") is True
    # as upstream, split_command strips redirections first, so `> HEAD` is never seen here
    assert ro.command_writes_to_git_internal_paths("echo x > HEAD") is False


def test_is_bash_read_only_uses_the_given_cwd(tmp_path):
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "objects").mkdir()
    (tmp_path / "refs").mkdir()
    assert ro.is_bash_read_only({"command": "git status"}, cwd=str(tmp_path)) is False
    assert ro.is_bash_read_only({"command": "git status"}, cwd=os.getcwd()) is True


# ── bash_security: quote chain and quoted newline ────────────────────────────

def test_flag_in_quote_chain_letter_after_dash_prefix():
    assert bs._flag_in_quote_chain('" X"', 0, "-") is True


def test_flag_in_quote_chain_dash_prefixed_continuation():
    assert bs._flag_in_quote_chain('" "a', 0, "-") is True


def test_obfuscated_flags_chain_starts_after_closing_quote():
    command = "ls '-'\" \""
    assert bs.validate_obfuscated_flags(bs.build_validation_context(command)).behavior == "passthrough"


QUOTED_NEWLINE_ASK = (
    "Command contains a quoted newline followed by a #-prefixed line, "
    "which can hide arguments from line-based permission checks"
)


def test_quoted_newline_hash_line_asks():
    res = bs.validate_quoted_newline(bs.build_validation_context('echo "a\n# x"'))
    assert (res.behavior, res.message) == ("ask", QUOTED_NEWLINE_ASK)


# ── tools/bash.py ────────────────────────────────────────────────────────────

def test_zero_max_timeout_override_is_ignored():
    assert bash_mod.get_max_bash_timeout_ms({"BASH_MAX_TIMEOUT_MS": "0"}) == bash_mod.MAX_TIMEOUT_MS


def test_permission_cwd_without_a_workspace_attribute():
    assert bash_mod.permission_cwd(SimpleNamespace()) == os.getcwd()  # type: ignore[arg-type]


def test_call_without_a_workspace_attribute():
    tool = bash_mod.build_bash_tool()
    res = asyncio.run(tool.call({"command": "ls"}, SimpleNamespace()))  # type: ignore[arg-type]
    assert (res.is_error, res.content) == (True, "No workspace available")


def test_prompt_vars_are_rendered(monkeypatch):
    monkeypatch.setattr(bash_mod, "BASH_PROMPT", "run {{X}} now")
    assert bash_mod.build_bash_tool({"X": "1"}).prompt == "run 1 now"
    assert bash_mod.build_bash_tool().prompt == "run {{X}} now"


def test_validate_flags_positional_does_not_skip_or_stop():
    assert validate_flags(["c", "x", "-Z"], 1, CommandConfig(safe_flags={})) is False


def test_xargs_flags_before_the_target_are_validated_not_targeted():
    assert safe("xargs -I {} echo {}") is True
