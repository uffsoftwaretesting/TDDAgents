"""
Port of claude-code's Bash injection battery (`bashSecurity.ts`, legacy path).

Attack strings come from the upstream source comments wherever one is given, so each
test pins the bypass that motivated its validator.
"""

import pytest

from app.loop.permissions import bash_security as bs
from app.loop.permissions.bash_security import (
    NON_MISPARSING_VALIDATORS,
    VALIDATORS,
    EARLY_VALIDATORS,
    ZSH_DANGEROUS_COMMANDS,
    bash_command_is_safe,
    build_validation_context,
    extract_heredocs,
    extract_quoted_content,
    has_malformed_tokens,
    has_safe_heredoc_substitution,
    has_shell_quote_single_quote_bug,
    has_unescaped_char,
    injection_check_disabled,
    is_env_truthy,
    is_safe_heredoc,
    shell_quote_parse,
    strip_safe_heredoc_substitutions,
    strip_safe_redirections,
    try_parse_shell_command,
)


def ctx(command):
    return build_validation_context(command)


def verdict(command):
    r = bash_command_is_safe(command)
    return r.behavior, r.message, r.is_bash_security_check_for_misparsing


def ask(message, misparsing=True):
    return ("ask", message, misparsing)


PASSED = ("passthrough", "Command passed all security checks", False)


# ── battery composition ──────────────────────────────────────────────────────

def test_validator_order_matches_upstream():
    assert [v.__name__ for v in EARLY_VALIDATORS] == [
        "validate_empty", "validate_incomplete_commands", "validate_safe_command_substitution", "validate_git_commit",
    ]
    assert [v.__name__ for v in VALIDATORS] == [
        "validate_jq_command", "validate_obfuscated_flags", "validate_shell_metacharacters",
        "validate_dangerous_variables", "validate_comment_quote_desync", "validate_quoted_newline",
        "validate_carriage_return", "validate_newlines", "validate_ifs_injection", "validate_proc_environ_access",
        "validate_dangerous_patterns", "validate_redirections", "validate_backslash_escaped_whitespace",
        "validate_backslash_escaped_operators", "validate_unicode_whitespace", "validate_mid_word_hash",
        "validate_brace_expansion", "validate_zsh_dangerous_commands", "validate_malformed_token_injection",
    ]
    assert NON_MISPARSING_VALIDATORS == {bs.validate_newlines, bs.validate_redirections}


@pytest.mark.parametrize("command", [
    "ls -la", "git status", "pytest -q tests/", "python -m pytest 2>&1", "cmd > /dev/null",
    "find . -name '*.py'", "grep -rn 'def foo' app/", "echo \"hello world\"", "cat file.txt | grep x",
    "npm install && npm test", "awk '{print $1}' file", "ls -- \"-\"*", "cut -d'\"' -f1 x",
])
def test_benign_commands_pass(command):
    assert verdict(command) == PASSED


# ── pre-checks ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", ["echo safe\x00; rm -rf /", "ls\x07", "a\x0bb", "x\x1f", "y\x7f"])
def test_control_characters(command):
    assert verdict(command) == ask(
        "Command contains non-printable control characters that could be used to bypass security checks"
    )


@pytest.mark.parametrize("command", ["echo 'a\tb'", "echo a\nb"])
def test_tab_and_newline_are_not_control_characters(command):
    assert bs.CONTROL_CHAR_RE.search(command) is None


@pytest.mark.parametrize("command, expected", [
    ("echo '\\' payload '\\'", True),
    ("echo 'abc\\'", True),
    ("echo '\\\\\\'", True),
    ("git ls-remote 'safe\\\\' '--upload-pack=evil' 'repo'", True),
    ("echo '\\\\'", False),
    ("echo 'plain'", False),
    ("echo \"a\\\"b\" 'c'", False),
    ("echo \\' x", False),
    ("echo \"it's\"", False),
])
def test_shell_quote_single_quote_bug(command, expected):
    assert has_shell_quote_single_quote_bug(command) is expected


def test_single_quote_bug_verdict():
    assert verdict("echo 'abc\\'") == ask(
        "Command contains single-quoted backslash pattern that could bypass security checks"
    )


# ── extractQuotedContent / stripSafeRedirections / hasUnescapedChar ─────────

@pytest.mark.parametrize("command, expected", [
    ("echo 'a b' \"c d\" e", ("echo  c d e", "echo   e", "echo '' \"\" e")),
    ("a\\'b", ("a\\'b", "a\\'b", "a\\'b")),
    ("\"x\\\"y\"", ("x\\\"y", "", "\"\"")),
    ("'it\\'s'", ("s", "s", "''s'")),
])
def test_extract_quoted_content(command, expected):
    assert extract_quoted_content(command) == expected


def test_extract_quoted_content_jq_keeps_double_quotes():
    # Upstream appends the quote char for jq without `continue`, so the closing quote also
    # lands in fullyUnquoted and is recorded twice in unquotedKeepQuoteChars.
    assert extract_quoted_content('jq ".a"', True) == ('jq ".a"', 'jq "', 'jq """')


@pytest.mark.parametrize("content, expected", [
    ("cmd 2>&1", "cmd"),
    ("cmd 2 >& 1 x", "cmd x"),
    ("cmd > /dev/null", "cmd"),
    ("cmd 2>/dev/null", "cmd "),
    ("cmd < /dev/null", "cmd"),
    ("echo hi > /dev/nullo", "echo hi > /dev/nullo"),
    ("cmd 2>&1x", "cmd 2>&1x"),
    ("cmd </dev/nullx", "cmd </dev/nullx"),
])
def test_strip_safe_redirections(content, expected):
    assert strip_safe_redirections(content) == expected


@pytest.mark.parametrize("content, expected", [
    ("test \\`safe\\`", False),
    ("test `dangerous`", True),
    ("test\\\\`date`", True),
    ("trailing\\", False),
    ("`", True),
])
def test_has_unescaped_char(content, expected):
    assert has_unescaped_char(content, "`") is expected


def test_has_unescaped_char_rejects_multi_char():
    with pytest.raises(ValueError, match="only works with single characters"):
        has_unescaped_char("abc", "ab")


def test_build_validation_context_fields():
    c = ctx("jq '.x' > /dev/null")
    assert c.original_command == "jq '.x' > /dev/null"
    assert c.base_command == "jq"
    assert c.fully_unquoted_pre_strip == "jq  > /dev/null"
    assert c.fully_unquoted_content == "jq"
    assert c.unquoted_keep_quote_chars == "jq '' > /dev/null"
    assert ctx("").base_command == ""


# ── early validators ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", ["", "   ", "\n"])
def test_validate_empty(command):
    r = bs.validate_empty(ctx(command))
    assert r.behavior == "allow"
    assert r.updated_input == {"command": command}
    assert r.decision_reason == {"type": "other", "reason": "Empty command is safe"}
    assert verdict(command) == ("passthrough", "Empty command is safe", False)


def test_validate_empty_passthrough():
    assert bs.validate_empty(ctx("ls")).message == "Command is not empty"


@pytest.mark.parametrize("command, message", [
    ("\tls", "Command appears to be an incomplete fragment (starts with tab)"),
    ("  \tls", "Command appears to be an incomplete fragment (starts with tab)"),
    ("-rf /", "Command appears to be an incomplete fragment (starts with flags)"),
    ("  --help", "Command appears to be an incomplete fragment (starts with flags)"),
    ("&& rm x", "Command appears to be a continuation line (starts with operator)"),
    ("|| x", "Command appears to be a continuation line (starts with operator)"),
    ("; x", "Command appears to be a continuation line (starts with operator)"),
    (">> f", "Command appears to be a continuation line (starts with operator)"),
    ("> f", "Command appears to be a continuation line (starts with operator)"),
    ("< f", "Command appears to be a continuation line (starts with operator)"),
])
def test_validate_incomplete_commands(command, message):
    assert bs.validate_incomplete_commands(ctx(command)).message == message
    assert verdict(command) == ask(message)


def test_validate_incomplete_commands_passthrough():
    r = bs.validate_incomplete_commands(ctx("ls -a"))
    assert (r.behavior, r.message) == ("passthrough", "Command appears complete")


SAFE_HEREDOC = "git commit -m \"$(cat <<'EOF'\nmessage body\nEOF\n)\""


@pytest.mark.parametrize("command", [
    SAFE_HEREDOC,
    "echo $(cat <<'EOF'\nhello\nEOF\n)",
    "echo $(cat <<'EOF'\nhello\nEOF)",
    "echo $(cat <<\\EOF\nhello\nEOF\n)",
    "echo $(cat <<-'EOF'\n\thello\n\tEOF\n)",
    "echo $(cat <<-'EOF'\nhi\n\tEOF)",
    "echo $(cat <<''EOF''\nhello\nEOF\n)",
])
def test_safe_heredoc_early_allow(command):
    assert is_safe_heredoc(command) is True
    r = bs.validate_safe_command_substitution(ctx(command))
    assert r.behavior == "allow"
    assert r.updated_input == {"command": command}
    assert verdict(command) == (
        "passthrough", "Safe command substitution: cat with quoted/escaped heredoc delimiter", False,
    )


@pytest.mark.parametrize("command", [
    "echo $(cat <<EOF\n$(id)\nEOF\n)",                       # unquoted delimiter expands
    "$(cat <<'EOF'\nchmod\nEOF\n) 777 /etc/shadow",          # command-name position
    "echo $(cat <<'EOF'\nx\nEOF\n) ; rm -rf /",              # metachar after
    "echo $(cat <<'EOF' ; rm -rf /\nx\nEOF\n)",              # content on open line
    "echo $(cat <<'EOF'",                                    # no body
    "echo $(cat <<'EOF'\nx\nEOF",                            # no closing paren line
    "echo $(cat <<'EOF'\nx\nEOF\nnot-a-paren",               # paren not at line start
    "echo $(cat <<'EOF'\nx\nEOF; evil\n)",                   # early-closure metachar
    "echo $(cat <<'EOF'\nx\n)",                              # delimiter never closes
    "zmodload zsh/system $(cat <<'EOF'\nx\nEOF\n)",          # remainder must pass battery
    "echo $(cat <<'A'\n$(cat <<'B'\nx\nB\n)\nA\n)",          # nested ranges
    "echo $(cat <<'EOF'\nx\nEOF\n)\u00a0y",                  # non-ASCII in remainder
])
def test_unsafe_heredoc_patterns(command):
    assert is_safe_heredoc(command) is False


def test_is_safe_heredoc_requires_substitution_and_pattern():
    assert is_safe_heredoc("cat <<'EOF'\nx\nEOF") is False
    assert is_safe_heredoc("echo $(ls <<x)") is False
    assert bs.validate_safe_command_substitution(ctx("ls")).message == "No heredoc in substitution"
    assert bs.validate_safe_command_substitution(
        ctx("echo $(cat <<EOF\nx\nEOF\n)")
    ).message == "Command substitution needs validation"


def test_safe_heredoc_with_trailing_text_after_prefix_is_allowed():
    assert is_safe_heredoc("echo $(cat <<'EOF'\nx\nEOF\n) tail") is True


def test_safe_heredoc_with_two_heredocs():
    cmd = "echo $(cat <<'A'\na\nA\n) $(cat <<'B'\nb\nB\n)"
    assert is_safe_heredoc(cmd) is True


@pytest.mark.parametrize("command, expected", [
    ("echo $(cat <<'EOF'\nhello\nEOF\n) && ls", "echo  && ls"),
    ("echo $(cat <<'EOF'\nhello\nEOF) x", "echo  x"),
    ("echo $(cat <<-'EOF'\n\thi\n\tEOF\n)", "echo "),
    ("a $(cat <<'A'\n1\nA\n) b $(cat <<'B'\n2\nB\n) c", "a  b  c"),
])
def test_strip_safe_heredoc_substitutions(command, expected):
    assert strip_safe_heredoc_substitutions(command) == expected
    assert has_safe_heredoc_substitution(command) is True


@pytest.mark.parametrize("command", [
    "ls",
    "echo \\$(cat <<'EOF'\nx\nEOF\n)",
    "echo $(cat <<EOF\nx\nEOF\n)",
    "echo $(cat <<'EOF'",
    "echo $(cat <<'EOF' x\nbody\nEOF\n)",
    "echo $(cat <<'EOF'\nbody\nEOF\nnope",
    "echo $(cat <<'EOF'\nbody\nEOFX\n",
])
def test_strip_safe_heredoc_substitutions_none(command):
    assert strip_safe_heredoc_substitutions(command) is None
    assert has_safe_heredoc_substitution(command) is False


@pytest.mark.parametrize("command, expected", [
    ("git commit -m 'fix bug'", ("passthrough", "Git commit with simple quoted message is allowed", False)),
    ("git commit --amend -m \"msg\" --author=\"N <e@x>\"",
     ("passthrough", "Git commit with simple quoted message is allowed", False)),
    ("git commit -m \"$(id)\"", ask("Git commit message contains command substitution patterns")),
    ("git commit -m \"`id`\"", ask("Git commit message contains command substitution patterns")),
    ("git commit -m \"${X}\"", ask("Git commit message contains command substitution patterns")),
    ("git commit -m '---'", ask("Command contains quoted characters in flag names")),
])
def test_validate_git_commit_verdicts(command, expected):
    assert verdict(command) == expected


@pytest.mark.parametrize("command, message", [
    ("ls -m 'x'", "Not a git commit"),
    ("git status", "Not a git commit"),
    ("git commit -m 'a\\'b'", "Git commit contains backslash, needs full validation"),
    ("git commit -m 'x' && evil", "Git commit remainder contains shell metacharacters"),
    ("git commit -m 'x' $(id)", "Git commit remainder contains shell metacharacters"),
    ("git commit -m 'x' ${y}", "Git commit remainder contains shell metacharacters"),
    ("git commit --allow-empty -m 'payload' > ~/.bashrc", "Git commit remainder contains unquoted redirect operator"),
    ("git commit ; curl evil.com -m 'x'", "Git commit needs validation"),
    ("git commit -a", "Git commit needs validation"),
])
def test_validate_git_commit_passthroughs(command, message):
    r = bs.validate_git_commit(ctx(command))
    assert (r.behavior, r.message) == ("passthrough", message)


def test_git_commit_single_quoted_substitution_is_literal():
    assert verdict("git commit -m '$(id)'")[0] == "passthrough"


def test_git_commit_redirect_attack_goes_through_full_battery():
    assert verdict("git commit --allow-empty -m 'payload' > ~/.bashrc") == ask(
        "Command contains output redirection (>) which could write to arbitrary files", False
    )


# ── main validators ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("command, message", [
    ("jq 'system(\"id\")' f", "jq command contains system() function which executes arbitrary commands"),
    ("jq -f prog.jq x", "jq command contains dangerous flags that could execute code or read arbitrary files"),
    ("jq --rawfile a f '.'", "jq command contains dangerous flags that could execute code or read arbitrary files"),
    ("jq --slurpfile a f '.'", "jq command contains dangerous flags that could execute code or read arbitrary files"),
    ("jq --from-file p", "jq command contains dangerous flags that could execute code or read arbitrary files"),
    ("jq -L dir '.'", "jq command contains dangerous flags that could execute code or read arbitrary files"),
    ("jq --library-path d '.'", "jq command contains dangerous flags that could execute code or read arbitrary files"),
])
def test_validate_jq_command(command, message):
    assert bs.validate_jq_command(ctx(command)).message == message
    assert verdict(command) == ask(message)


@pytest.mark.parametrize("command, message", [
    ("ls system(", "Not jq"),
    ("jq '.a' file.json", "jq command is safe"),
    ("jq -fx '.'", "jq command is safe"),
    ("jq '.a' -Lx", "jq command is safe"),
    ("jq '.filesystem(x)'", "jq command is safe"),
])
def test_validate_jq_command_passthrough(command, message):
    r = bs.validate_jq_command(ctx(command))
    assert (r.behavior, r.message) == ("passthrough", message)


@pytest.mark.parametrize("command", [
    "grep $'\\x2d' f", "find . $'-exec'", "ls $''-la",
])
def test_ansi_c_quoting(command):
    assert verdict(command) == ask("Command contains ANSI-C quoting which can hide characters")


def test_locale_quoting():
    assert verdict('find . $"-exec"') == ask("Command contains locale quoting which can hide characters")


def test_empty_special_quotes_before_dash():
    c = ctx("find . $\"\" -exec")
    # The locale-quoting check fires first on `$""`.
    assert bs.validate_obfuscated_flags(c).message == "Command contains locale quoting which can hide characters"
    c2 = ctx("find . $'' x")
    assert bs.validate_obfuscated_flags(c2).message == "Command contains ANSI-C quoting which can hide characters"


@pytest.mark.parametrize("command, message", [
    ("find . ''-exec x", "Command contains empty quotes before dash (potential bypass)"),
    ("find . \"\" -exec x", "Command contains empty quotes before dash (potential bypass)"),
    ("find . ''\"\"-exec", "Command contains empty quotes before dash (potential bypass)"),
    ("jq \"\"\"-f\" x", "Command contains empty quote pair adjacent to quoted dash (potential flag obfuscation)"),
    ("jq $x''\"-f\" x", "Command contains empty quote pair adjacent to quoted dash (potential flag obfuscation)"),
    ("find . \"\"\"x\"-f", "Command contains consecutive quote characters at word start (potential obfuscation)"),
])
def test_empty_quote_obfuscation(command, message):
    assert bs.validate_obfuscated_flags(ctx(command)).message == message


@pytest.mark.parametrize("command", [
    "find . \"-exec\" rm",
    "find . \"-\"exec rm",
    "find . \"--\"output x",
    "find . \"-\"\"exec\" rm",
    "find . \"-\"\"-\"exec rm",
    "find . \"\"\"-\"exec",
    "find . \"-\"$VAR",
    "find . \"-\"{exec,delete}",
    "find . \"-\"\\exec",
    "find . \"-\"-output",
    "find . \"-\"`echo exec`",
    "find . '-'output",
    "find . \"-$VAR\"",
    "find . \"-\"\"$*\"",
    "find . -e\"xec\"",
    "jq '\\' \"-f\" evil",
])
def test_quoted_flag_names(command):
    r = bs.validate_obfuscated_flags(ctx(command))
    assert r.behavior == "ask"


@pytest.mark.parametrize("command", [
    "echo \"-flag\"",
    "make test TEST=\"file.py -v\"",
    "echo '---'",
    "ls -- \"-\"*",
    "cut -d'\"' -f1",
    "cut -d\",\" -f1 file",
    "printf '%s' \"---\" x",
    "grep -e 'pattern' f",
    "ls -la",
])
def test_quoted_flag_benign(command):
    assert bs.validate_obfuscated_flags(ctx(command)).behavior == "passthrough"


def test_obfuscated_flags_echo_exception_only_without_operators():
    assert bs.validate_obfuscated_flags(ctx("echo $'x'")).message == (
        "echo command is safe and has no dangerous flags"
    )
    assert bs.validate_obfuscated_flags(ctx("echo x; ls $'-x'")).message == (
        "Command contains ANSI-C quoting which can hide characters"
    )
    assert bs.validate_obfuscated_flags(ctx("ls")).message == "No obfuscated flags detected"


def test_obfuscated_flags_fully_unquoted_fallbacks():
    c = bs.ValidationContext("x", "x", "x", " '-a", " '-a", "x")
    assert bs.validate_obfuscated_flags(c).message == "Command contains quoted characters in flag names"
    c = bs.ValidationContext("x", "x", "x", "\"\"-a", "\"\"-a", "x")
    assert bs.validate_obfuscated_flags(c).message == "Command contains quoted characters in flag names"


def test_obfuscated_flags_dash_chain_trailing_chars():
    assert bs._flag_in_quote_chain('"-" ', 0, "") is False
    assert bs._flag_in_quote_chain('""-', 0, "") is True
    assert bs._flag_in_quote_chain('"-"x', 0, "") is True
    assert bs._flag_in_quote_chain('"x"y', 0, "-") is True
    assert bs._flag_in_quote_chain('""a', 0, "") is False
    assert bs._flag_in_quote_chain('""a', 0, "-") is True
    assert bs._flag_in_quote_chain('"', 0, "") is False
    assert bs._flag_in_quote_chain('""', 0, "-") is False
    assert bs._flag_in_quote_chain('"" x', 0, "-") is False
    assert bs._flag_in_quote_chain("'' -", 0, "") is False


@pytest.mark.parametrize("command", [
    # The validator reads withDoubleQuotes, which keeps double-quote characters only for jq,
    # so (exactly as upstream) it can only fire on jq programs.
    "jq \".a;b\" f",
    "jq \"a&b\"",
    "jq -name \"x;y\"",
    "jq -path \"a|b\"",
    "jq -iname \"a&b\"",
    "jq -regex \"a;b\"",
])
def test_validate_shell_metacharacters(command):
    r = bs.validate_shell_metacharacters(ctx(command))
    assert r.message == "Command contains shell metacharacters (;, |, or &) in arguments"


@pytest.mark.parametrize("command", [
    "jq \"a|b\" x", "jq -regex \"a|b\"", "grep 'x' y", "echo a;b", "find . -name 'x;y'", "echo 'a;b' x",
])
def test_validate_shell_metacharacters_passthrough(command):
    assert bs.validate_shell_metacharacters(ctx(command)).message == "No metacharacters"


def test_shell_metacharacters_verdict():
    assert verdict("jq -name \"x;y\"") == ask("Command contains shell metacharacters (;, |, or &) in arguments")


@pytest.mark.parametrize("command", ["cat < $FILE", "echo x > $OUT", "echo x | $CMD", "$CMD | cat", "$X>f", "$X <f"])
def test_validate_dangerous_variables(command):
    assert bs.validate_dangerous_variables(ctx(command)).message == (
        "Command contains variables in dangerous contexts (redirections or pipes)"
    )


@pytest.mark.parametrize("command", ["echo $HOME", "echo '$X | y'", "echo x | cat", "echo $1 | x"])
def test_validate_dangerous_variables_passthrough(command):
    assert bs.validate_dangerous_variables(ctx(command)).message == "No dangerous variables"


@pytest.mark.parametrize("command", [
    "echo \"it's\" # ' \" <<'MARKER'\nrm -rf /\nMARKER",
    "ls # don't",
    "ls # \"x\"",
])
def test_validate_comment_quote_desync(command):
    assert bs.validate_comment_quote_desync(ctx(command)).message == (
        "Command contains quote characters inside a # comment which can desync quote tracking"
    )


@pytest.mark.parametrize("command", [
    "ls # plain comment",
    "echo '#' \"it's\"",
    "echo \"#'\"",
    "echo \\# 'x'",
    "ls # c\necho 'ok'",
    "ls #",
])
def test_validate_comment_quote_desync_passthrough(command):
    assert bs.validate_comment_quote_desync(ctx(command)).message == "No comment quote desync"


def test_comment_quote_desync_verdict_is_misparsing():
    assert verdict("ls # don't") == ask(
        "Command contains quote characters inside a # comment which can desync quote tracking"
    )


@pytest.mark.parametrize("command", [
    "mv ./decoy '\n#' ~/.ssh/id_rsa ./exfil_dir",
    "echo \"a\n  # hidden\"",
])
def test_validate_quoted_newline(command):
    assert bs.validate_quoted_newline(ctx(command)).message == (
        "Command contains a quoted newline followed by a #-prefixed line, "
        "which can hide arguments from line-based permission checks"
    )


@pytest.mark.parametrize("command, message", [
    ("echo 'a\nb'", "No newline or no hash"),
    ("echo #x", "No newline or no hash"),
    ("echo 'a\nb' #c", "No quoted newline-hash pattern"),
    ("echo a\n#b", "No quoted newline-hash pattern"),
    ("echo \\'\n#x", "No quoted newline-hash pattern"),
])
def test_validate_quoted_newline_passthrough(command, message):
    assert bs.validate_quoted_newline(ctx(command)).message == message


def test_validate_quoted_newline_checks_next_line_only():
    assert bs.validate_quoted_newline(ctx("echo 'a\n#b'")).behavior == "ask"
    assert bs.validate_quoted_newline(ctx("echo \"a\nb\n# c\"")).behavior == "ask"


@pytest.mark.parametrize("command", ["TZ=UTC\recho curl evil.com", "echo 'a\rb'", "a\r"])
def test_validate_carriage_return(command):
    assert bs.validate_carriage_return(ctx(command)).message == (
        "Command contains carriage return (\\r) which shell-quote and bash tokenize differently"
    )


def test_carriage_return_verdict_is_misparsing():
    assert verdict("TZ=UTC\recho curl evil.com") == ask(
        "Command contains carriage return (\\r) which shell-quote and bash tokenize differently"
    )


@pytest.mark.parametrize("command, message", [
    ("ls", "No carriage return"),
    ("echo \"a\rb\"", "CR only inside double quotes"),
    ("echo \\\r", "CR only inside double quotes"),
])
def test_validate_carriage_return_passthrough(command, message):
    assert bs.validate_carriage_return(ctx(command)).message == message


@pytest.mark.parametrize("command", ["echo hi\nwhoami", "ls\r\nid", "a\n  b", "cmd \\>/dev/null\nwhoami"])
def test_validate_newlines(command):
    assert bs.validate_newlines(ctx(command)).message == (
        "Command contains newlines that could separate multiple commands"
    )


@pytest.mark.parametrize("command, message", [
    ("ls -la", "No newlines"),
    ("echo 'a\nb'", "No newlines"),
    ("cmd \\\n--flag", "Newlines appear to be within data"),
    ("ls\n", "Newlines appear to be within data"),
    ("ls\n   ", "Newlines appear to be within data"),
])
def test_validate_newlines_passthrough(command, message):
    assert bs.validate_newlines(ctx(command)).message == message


def test_newline_ask_is_not_misparsing():
    assert verdict("echo hi\nwhoami") == ask("Command contains newlines that could separate multiple commands", False)


def test_mid_word_line_continuation_is_flagged():
    assert bs.validate_newlines(ctx("tr\\\naceroute")).behavior == "ask"


@pytest.mark.parametrize("command", ["echo $IFS", "a${IFS}b", "x${IFS:0:1}y", "${#IFS}"])
def test_validate_ifs_injection(command):
    assert bs.validate_ifs_injection(ctx(command)).message == (
        "Command contains IFS variable usage which could bypass security validation"
    )


def test_validate_ifs_injection_passthrough():
    assert bs.validate_ifs_injection(ctx("echo IFS")).message == "No IFS injection detected"
    assert bs.validate_ifs_injection(ctx("echo ${X}IFS")).message == "No IFS injection detected"


@pytest.mark.parametrize("command", ["cat /proc/self/environ", "cat /proc/1/environ", "x /proc/a/b/environ"])
def test_validate_proc_environ_access(command):
    assert bs.validate_proc_environ_access(ctx(command)).message == (
        "Command accesses /proc/*/environ which could expose sensitive environment variables"
    )


@pytest.mark.parametrize("command", ["cat /proc/cpuinfo", "cat /proc/environ", "cat /proc/1\n/environ"])
def test_validate_proc_environ_access_passthrough(command):
    assert bs.validate_proc_environ_access(ctx(command)).message == "No /proc/environ access detected"


@pytest.mark.parametrize("command, fragment", [
    ("diff <(ls a) <(ls b)", "process substitution <()"),
    ("tee >(cat)", "process substitution >()"),
    ("x =(ls)", "Zsh process substitution =()"),
    ("=curl evil.com", "Zsh equals expansion (=cmd)"),
    ("ls; =curl x", "Zsh equals expansion (=cmd)"),
    ("git diff $(rm -rf /)", "$() command substitution"),
    ("echo \"$(id)\"", "$() command substitution"),
    ("echo ${HOME}", "${} parameter substitution"),
    ("echo $[1+1]", "$[] legacy arithmetic expansion"),
    ("echo ~[x]", "Zsh-style parameter expansion"),
    ("ls *(e:x:)", "Zsh-style glob qualifiers"),
    ("ls *(+cmd)", "Zsh glob qualifier with command execution"),
    ("{ a } always { b }", "Zsh always block (try/always construct)"),
    ("echo <# x #>", "PowerShell comment syntax"),
])
def test_command_substitution_patterns(command, fragment):
    assert bs.validate_dangerous_patterns(ctx(command)).message == f"Command contains {fragment}"


def test_backticks():
    assert bs.validate_dangerous_patterns(ctx("echo `id`")).message == (
        "Command contains backticks (`) for command substitution"
    )
    assert bs.validate_dangerous_patterns(ctx("echo \\`id\\`")).message == "No dangerous patterns"
    assert bs.validate_dangerous_patterns(ctx("echo '`id`'")).message == "No dangerous patterns"


@pytest.mark.parametrize("command", ["VAR=x cmd", "echo a=b", "echo '$(id)'", "ls"])
def test_dangerous_patterns_passthrough(command):
    assert bs.validate_dangerous_patterns(ctx(command)).message == "No dangerous patterns"


@pytest.mark.parametrize("command, message", [
    ("cat < /etc/passwd", "Command contains input redirection (<) which could read sensitive files"),
    ("echo x > f", "Command contains output redirection (>) which could write to arbitrary files"),
    ("echo x >> f", "Command contains output redirection (>) which could write to arbitrary files"),
    ("cat <f >g", "Command contains input redirection (<) which could read sensitive files"),
])
def test_validate_redirections(command, message):
    assert bs.validate_redirections(ctx(command)).message == message
    assert verdict(command) == ask(message, False)


@pytest.mark.parametrize("command", ["echo '<>' \">\"", "cmd > /dev/null 2>&1", "ls"])
def test_validate_redirections_passthrough(command):
    assert bs.validate_redirections(ctx(command)).message == "No redirections"


@pytest.mark.parametrize("command, expected", [
    ("echo\\ test/../../../usr/bin/touch /tmp/file", True),
    ("a\\\tb", True),
    ("echo 'a\\ b'", False),
    ("echo \"a\\ b\"", False),
    ("echo \\\\ x", False),
    ("echo \"\\\"\" \\ x", True),
    ("echo 'x' \\ y", True),
    ("trailing\\", False),
])
def test_backslash_escaped_whitespace(command, expected):
    assert bs.has_backslash_escaped_whitespace(command) is expected


def test_validate_backslash_escaped_whitespace_messages():
    assert bs.validate_backslash_escaped_whitespace(ctx("a\\ b")).message == (
        "Command contains backslash-escaped whitespace that could alter command parsing"
    )
    assert bs.validate_backslash_escaped_whitespace(ctx("a b")).message == "No backslash-escaped whitespace"


@pytest.mark.parametrize("command, expected", [
    ("cat safe.txt \\; echo ~/.ssh/id_rsa", True),
    ("find . -exec cmd {} \\;", True),
    ("a \\| b", True),
    ("a \\& b", True),
    ("a \\< b", True),
    ("a \\> b", True),
    ("a \\\\; b", False),
    ("echo \"\\;\"", False),
    ("echo '\\;'", False),
    ("tac \"x\\\"y\" \\; echo ~/.ssh/id_rsa", True),
    ("cat \"x\\\\\" \\; echo /etc/passwd", True),
    ("find . \\( -name x \\)", False),
    ("x\\", False),
])
def test_backslash_escaped_operator(command, expected):
    assert bs.has_backslash_escaped_operator(command) is expected


def test_validate_backslash_escaped_operators_messages():
    assert bs.validate_backslash_escaped_operators(ctx("a \\; b")).message == (
        "Command contains a backslash before a shell operator (;, |, &, <, >) which can hide command structure"
    )
    assert bs.validate_backslash_escaped_operators(ctx("a b")).message == "No backslash-escaped operators"


def test_deferred_non_misparsing_does_not_shadow_misparsing():
    """`cat safe.txt \\; echo /etc/passwd > ./out` must return the misparsing ask."""
    assert verdict("cat safe.txt \\; echo /etc/passwd > ./out") == ask(
        "Command contains a backslash before a shell operator (;, |, &, <, >) which can hide command structure"
    )


def test_first_deferred_non_misparsing_wins():
    assert verdict("echo a\nb > f") == ask("Command contains newlines that could separate multiple commands", False)


@pytest.mark.parametrize("content, pos, expected", [
    ("a{", 1, False), ("\\{", 1, True), ("\\\\{", 2, False), ("\\\\\\{", 3, True), ("{", 0, False),
])
def test_is_escaped_at_position(content, pos, expected):
    assert bs.is_escaped_at_position(content, pos) is expected


@pytest.mark.parametrize("command, message", [
    ("git ls-remote {--upload-pack=\"touch /tmp/test\",test}",
     "Command contains brace expansion that could alter command parsing"),
    ("echo {1..5}", "Command contains brace expansion that could alter command parsing"),
    ("echo {--upload-pack=\"evil\",{test}}", "Command contains brace expansion that could alter command parsing"),
    ("echo {a{b,c}}", "Command contains brace expansion that could alter command parsing"),
    ("git diff {@'{'0},--output=/tmp/pwned}",
     "Command has excess closing braces after quote stripping, indicating possible brace expansion obfuscation"),
    ("echo {a'}'b,c}",
     "Command contains quoted brace character inside brace context (potential brace expansion obfuscation)"),
])
def test_validate_brace_expansion(command, message):
    assert bs.validate_brace_expansion(ctx(command)).message == message


@pytest.mark.parametrize("command", [
    "echo {}", "echo {foo}", "awk '{print $1}'", "echo \\{a,b}", "echo {a,b\\}", "echo {foo",
    "echo }", "echo '{' x", "echo {a.b}", "echo {a.}", "find . -exec x {} +", "echo {{x}",
])
def test_validate_brace_expansion_passthrough(command):
    assert bs.validate_brace_expansion(ctx(command)).message == "No brace expansion detected"


def test_excess_closing_braces():
    assert bs.validate_brace_expansion(ctx("echo {x}}")).behavior == "ask"


def test_brace_expansion_uses_pre_strip_content():
    assert bs.validate_brace_expansion(ctx("echo \\>/dev/null{a,b}")).behavior == "ask"


@pytest.mark.parametrize("char", ["\u00a0", "\u1680", "\u2000", "\u200a", "\u2028", "\u2029", "\u202f", "\u205f",
                                  "\u3000", "\ufeff"])
def test_validate_unicode_whitespace(char):
    assert bs.validate_unicode_whitespace(ctx(f"ls{char}-la")).message == (
        "Command contains Unicode whitespace characters that could cause parsing inconsistencies"
    )


def test_validate_unicode_whitespace_passthrough():
    assert bs.validate_unicode_whitespace(ctx("ls -la\t\u200b")).message == "No Unicode whitespace"


@pytest.mark.parametrize("command", ["echo a#b", "echo 'x'#", "foo\\\n#bar", "echo \\\\\\\n#x"])
def test_validate_mid_word_hash(command):
    assert bs.validate_mid_word_hash(ctx(command)).message == (
        "Command contains mid-word # which is parsed differently by shell-quote vs bash"
    )


@pytest.mark.parametrize("command", ["echo ${#var}", "ls # comment", "#x", "echo '#'", "foo\\\\\n#bar"])
def test_validate_mid_word_hash_passthrough(command):
    assert bs.validate_mid_word_hash(ctx(command)).message == "No mid-word hash"


@pytest.mark.parametrize("command", sorted(ZSH_DANGEROUS_COMMANDS))
def test_validate_zsh_dangerous_commands(command):
    assert bs.validate_zsh_dangerous_commands(ctx(f"{command} x")).message == (
        f"Command uses Zsh-specific '{command}' which can bypass security checks"
    )


@pytest.mark.parametrize("command", ["FOO=bar command builtin zmodload x", "  noglob nocorrect ztcp h"])
def test_zsh_base_command_skips_assignments_and_modifiers(command):
    assert bs.validate_zsh_dangerous_commands(ctx(command)).behavior == "ask"


@pytest.mark.parametrize("command, behavior", [
    ("fc -e vim", "ask"), ("fc -le", "ask"), ("fc -l", "passthrough"), ("fc", "passthrough"),
    ("echo zmodload", "passthrough"), ("", "passthrough"), ("A=1", "passthrough"), ("x fc -e", "passthrough"),
])
def test_validate_zsh_fc(command, behavior):
    r = bs.validate_zsh_dangerous_commands(ctx(command))
    assert r.behavior == behavior
    if command.startswith("fc -") and behavior == "ask":
        assert r.message == "Command uses 'fc -e' which can execute arbitrary commands via editor"
    if behavior == "passthrough":
        assert r.message == "No Zsh dangerous commands"


def test_zsh_dangerous_command_set_matches_upstream():
    assert ZSH_DANGEROUS_COMMANDS == {
        "zmodload", "emulate", "sysopen", "sysread", "syswrite", "sysseek", "zpty", "ztcp", "zsocket",
        "mapfile", "zf_rm", "zf_mv", "zf_ln", "zf_chmod", "zf_chown", "zf_mkdir", "zf_rmdir", "zf_chgrp",
    }


@pytest.mark.parametrize("command, message", [
    ("echo x", "No command separators"),
    ("echo ${} ; ls", "Parse failed, handled elsewhere"),
    ("echo a; ls", "No malformed token injection detected"),
])
def test_validate_malformed_token_injection_passthrough(command, message):
    assert bs.validate_malformed_token_injection(ctx(command)).message == message


@pytest.mark.parametrize("command", [
    "echo {a ; ls", "echo [a && ls", "echo \"hi;evil | cat", "echo a' || b",
    "echo x\\\"y ; z",
])
def test_validate_malformed_token_injection(command):
    assert bs.validate_malformed_token_injection(ctx(command)).message == (
        "Command contains ambiguous syntax with command separators that could be misinterpreted"
    )


# ── shell-quote parse port ───────────────────────────────────────────────────

@pytest.mark.parametrize("command, tokens", [
    ("", []),
    ("echo 'a b' \"c $X d\" e", ["echo", "a b", "c  d", "e"]),
    ("a|b && c ; d || e", ["a", {"op": "|"}, "b", {"op": "&&"}, "c", {"op": ";"}, "d", {"op": "||"}, "e"]),
    ("a;;b", ["a", {"op": ";;"}, "b"]),
    ("a |& b", ["a", {"op": "|&"}, "b"]),
    ("a <<< b", ["a", {"op": "<<<"}, "b"]),
    ("a >> b >& c <& d <( e )", ["a", {"op": ">>"}, "b", {"op": ">&"}, "c", {"op": "<&"}, "d", {"op": "<("},
                                 "e", {"op": ")"}]),
    ("ls *.py x?", ["ls", {"op": "glob", "pattern": "*.py"}, {"op": "glob", "pattern": "x?"}]),
    ("ls '*.py'", ["ls", "*.py"]),
    ("a\\ b", ["a b"]),
    ("a\\*b", [{"op": "glob", "pattern": "a*b"}]),
    ("echo hi # comment here", ["echo", "hi", {"comment": " comment here"}]),
    ("echo x#y z", ["echo", "x", {"comment": "y z"}]),
    ("all'one'\"token\"", ["allonetoken"]),
    ("echo \"a\\\"b\\\\c\\$d\\e\"", ["echo", "a\"b\\c$d\\e"]),
    ("echo \"x\\", ["echo", "x"]),
    ("echo $HOME/x ${A}y $1z $1-z", ["echo", "/x", "y", "", "-z"]),
    ("echo $?x $@ $", ["echo", "", "", "$"]),
    ("echo ${A${B}C}d", ["echo", "d"]),
    ("echo \"$X\"y", ["echo", "y"]),
    ("echo $%", ["echo", "$%"]),
    ("'unterminated", ["unterminated"]),
])
def test_shell_quote_parse(command, tokens):
    assert shell_quote_parse(command) == tokens


@pytest.mark.parametrize("command, error", [
    ("echo ${}", "Bad substitution: ${}"),
    ("echo ${abc", "Bad substitution: abc"),
    ("echo \"${}\"", "Bad substitution: ${}"),
])
def test_shell_quote_parse_errors(command, error):
    with pytest.raises(bs.ShellParseError, match=error.replace("$", r"\$").replace("{", r"\{").replace("}", r"\}")):
        shell_quote_parse(command)
    result = try_parse_shell_command(command)
    assert result.success is False
    assert result.error == error
    assert result.tokens == []


def test_try_parse_shell_command_success():
    r = try_parse_shell_command("a b")
    assert r.success is True
    assert r.tokens == ["a", "b"]
    assert r.error == ""


def test_try_parse_shell_command_unknown_error(monkeypatch):
    def boom(cmd, env=None):
        raise RuntimeError()

    monkeypatch.setattr(bs, "shell_quote_parse", boom)
    r = try_parse_shell_command("x")
    assert (r.success, r.error) == (False, "Unknown parse error")


def test_comment_stops_further_tokens():
    assert shell_quote_parse("a # b ; c") == ["a", {"comment": " b ; c"}]


@pytest.mark.parametrize("command, parsed, expected", [
    ('echo "x', [], True),
    ("echo 'x", [], True),
    ("echo \\\" ok", [], False),
    ("echo 'a\"b'", [], False),
    ("x", ["{a"], True), ("x", ["a}"], True), ("x", ["(a"], True), ("x", ["a)"], True),
    ("x", ["[a"], True), ("x", ["a]"], True), ("x", ['a"'], True), ("x", ["a'"], True),
    ("x", ["{a}", "(b)", "[c]", '"d"', "'e'", "f\\\"", "g\\'"], False),
    ("x", [{"op": "{"}], False),
])
def test_has_malformed_tokens(command, parsed, expected):
    assert has_malformed_tokens(command, parsed) is expected


# ── extractHeredocs (quotedOnly) ─────────────────────────────────────────────

def test_extract_heredocs_replaces_quoted_body_with_placeholder():
    out = extract_heredocs("cat <<'EOF' && echo done\n$(id)\nEOF\nls", quoted_only=True)
    assert out.startswith("cat __HEREDOC_0_")
    assert out.endswith("__ && echo done\nls")
    assert "$(id)" not in out


def test_extract_heredocs_placeholder_salt_is_random():
    cmd = "cat <<'EOF'\nx\nEOF"
    assert extract_heredocs(cmd, quoted_only=True) != extract_heredocs(cmd, quoted_only=True)


def test_extract_heredocs_two_heredocs_on_separate_lines_are_numbered():
    out = extract_heredocs("cat <<'A'\n1\nA\ncat <<'B'\n2\nB", quoted_only=True)
    assert "__HEREDOC_0_" in out and "__HEREDOC_1_" in out
    assert out.index("__HEREDOC_0_") < out.index("__HEREDOC_1_")


@pytest.mark.parametrize("command", [
    "ls",
    "cat <<EOF\n$(id)\nEOF",                                   # unquoted: body must stay visible
    "cat $'x' <<'EOF'\nb\nEOF",                                # ANSI-C quoting bail
    "echo `x` <<'EOF'\nb\nEOF",                                # backtick before <<
    "(( x = 1 << 2 ))\nEOF",                                   # arithmetic
    "echo '<<EOF'\nx\nEOF",                                    # quoted operator
    "echo \"<<EOF\"\nx\nEOF",
    "# <<'EOF'\nrm -rf /\nEOF",                                # comment
    "\\<<'EOF'\nx\nEOF",                                       # escaped operator
    "cat <<'EO F'\nx\nEO F",                                   # unmatched closing quote
    "cat <<'EOF'a\nx\nEOF",                                    # word continues
    "cat <<'EOF' 'x\ny' ; z",                                  # logical line never ends
    "cat <<'EOF' && \\\nrm -rf /\ncontent\nEOF",               # continuation
    "cat <<'EOF'\nbody",                                       # no closing delimiter
    "cat <<'EOF'\nEOF) evil\nEOF",                             # early-closure metachar
    "cat <<EOF\n<<'SAFE'\n$(evil)\nSAFE\nEOF",                 # quoted inside skipped body
    "cat <<EOF <<'SAFE'\n$(evil_command)\nEOF\nsafe body\nSAFE",  # overlapping skipped range
    "cat <<'A' <<'B'\na\nA\nb\nB",                             # shared content start
    "cat <<EOF\nnever closes <<'X'\nX",                        # unbounded skipped body
    "cat <<<'EOF'\nx\nEOF",                                    # herestring, not heredoc
])
def test_extract_heredocs_leaves_command_unchanged(command):
    assert extract_heredocs(command, quoted_only=True) == command


def test_extract_heredocs_unquoted_when_not_quoted_only():
    out = extract_heredocs("cat <<EOF\nbody\nEOF", quoted_only=False)
    assert out.startswith("cat __HEREDOC_0_")


@pytest.mark.parametrize("command", [
    "cat <<\\EOF\n$(x)\nEOF",
    "cat <<\"EOF\"\n$(x)\nEOF",
    "cat <<-'EOF'\n\tbody\n\tEOF",
    "cat <<'EOF';\nbody\nEOF",
    "x=\"a\\\"b\" cat <<'EOF'\nbody\nEOF",
    "echo 'it''s' <<'EOF'\nbody\nEOF",
    "echo \\\\<<'EOF'\nbody\nEOF",
    "cat <<'EOF' \"multi\" x\nbody\nEOF",
    "cat <<'EOF' \"a\\\"b\"\nbody\nEOF",
    "cat <<'EOF' \\'x\nbody\nEOF",
    "echo '#' <<'EOF'\nbody\nEOF",
    "echo # x\ncat <<'EOF'\nbody\nEOF",
])
def test_extract_heredocs_extracts(command):
    out = extract_heredocs(command, quoted_only=True)
    assert "__HEREDOC_0_" in out
    assert "body" not in out and "$(x)" not in out


def test_extract_heredocs_nested_filtered():
    out = extract_heredocs("cat <<'A'\ncat <<'B'\nx\nB\nA", quoted_only=True)
    assert out.count("__HEREDOC_") == 1


def test_heredoc_body_hidden_from_validators_only_when_quoted():
    assert verdict("cat <<'EOF'\n$(id)\nEOF") == PASSED  # quoted body is literal text
    assert verdict("cat <<EOF\n$(id)\nEOF")[0] == "ask"
    assert bs.validate_dangerous_patterns(ctx("cat <<'EOF'\n$(id)\nEOF")).behavior == "passthrough"
    assert bs.validate_dangerous_patterns(ctx("cat <<EOF\n$(id)\nEOF")).behavior == "ask"


# ── bash_command_is_safe composition ─────────────────────────────────────────

def test_early_ask_carries_misparsing_flag():
    assert verdict("-rf /")[2] is True


def test_early_allow_becomes_passthrough_with_reason():
    assert verdict("git commit -m 'x'") == ("passthrough", "Git commit with simple quoted message is allowed", False)


def test_early_allow_without_reason_says_command_allowed(monkeypatch):
    from app.loop.permissions.types import PermissionResult

    def allow_no_reason(c):
        return PermissionResult(behavior="allow")

    monkeypatch.setattr(bs, "EARLY_VALIDATORS", (allow_no_reason,))
    assert verdict("ls") == ("passthrough", "Command allowed", False)


def test_early_deny_is_returned_unchanged(monkeypatch):
    from app.loop.permissions.types import PermissionResult

    deny = PermissionResult(behavior="deny", message="no")
    monkeypatch.setattr(bs, "EARLY_VALIDATORS", (lambda c: deny,))
    assert bash_command_is_safe("ls") is deny


def test_misparsing_wrapper_preserves_fields():
    from app.loop.permissions.types import PermissionResult

    src = PermissionResult(behavior="ask", updated_input={"a": 1}, message="m", decision_reason={"type": "x"})
    out = bs._misparsing(src)
    assert out == PermissionResult(
        behavior="ask", updated_input={"a": 1}, message="m", decision_reason={"type": "x"},
        is_bash_security_check_for_misparsing=True,
    )


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_injection_check_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", value)
    assert injection_check_disabled() is True


@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("no", False), ("2", False),
    ("1", True), ("true", True), ("Yes", True), ("ON", True), (" on\n", True),
])
def test_is_env_truthy(value, expected):
    assert is_env_truthy(value) is expected


def test_injection_check_enabled_by_default(monkeypatch):
    monkeypatch.delenv("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK", raising=False)
    assert injection_check_disabled() is False


# ── Mutation-driven cases ────────────────────────────────────────────────────
# Each group below pins behaviour a surviving mutant got wrong. Most loop mutants turn
# `continue` into `break`, so the inputs put a rejected candidate *before* a valid one.

def _ph(out):
    return "__HEREDOC_" in out


@pytest.mark.parametrize("command", [
    "echo '<<A' x <<'EOF'\nbody\nEOF",                      # quoted operator, then valid
    "# <<A\ncat <<'EOF'\nbody\nEOF",                         # commented operator, then valid
    "\\<<A x\ncat <<'EOF'\nbody\nEOF",                       # escaped operator, then valid
    "cat <<EOF\n<<'S'\nx\nS\nEOF\ncat <<'Q'\nbody\nQ",       # skipped body, then valid
    "cat <<'EO F'\nx\nEO F\ncat <<'Q'\nbody\nQ",             # unmatched quote, then valid
    "cat <<'A'b\nx\nA\ncat <<'Q'\nbody\nQ",                  # word continues, then valid
    "cat <<'A' \\\nrm\nA\ncat <<'Q'\nbody\nQ",               # continuation, then valid
    "cat <<'A'\nno close\ncat <<'Q'\nbody\nQ",               # never closes, then valid
    "cat <<EOF <<'SAFE'\n$(evil)\nEOF\nsafe\nSAFE\ncat <<'Z'\nbody\nZ",  # overlap, then valid
    "cat <<EOF\nx\nEOF\ncat <<'Q'\nbody\nQ",                 # unquoted then quoted
    "echo \\\\x '<<A' <<'EOF'\nbody\nEOF",
])
def test_extract_heredocs_keeps_scanning_after_a_rejected_candidate(command):
    out = extract_heredocs(command, quoted_only=True)
    assert _ph(out)
    assert "body" not in out


@pytest.mark.parametrize("command", [
    "echo \"<<A\nx\nA\n\"",          # inside double quotes
    "echo '<<A\nx\nA\n'",            # inside single quotes
    "echo \"a <<A\nx\nA\n\"",        # dq must stay open past its first char
    "echo 'a <<A\nx\nA\n'",          # sq must stay open past its first char
    "echo \\' <<A\nx\nA\n'",         # escaped quote ... then a real one hides <<A? no: see below
])
def test_extract_heredocs_respects_quote_state(command):
    expected_unchanged = not command.startswith("echo \\'")
    out = extract_heredocs(command)
    assert (out == command) is expected_unchanged


def test_extract_heredocs_empty_dq_then_operator():
    assert _ph(extract_heredocs("echo \"\"<<A\nx\nA\n"))


@pytest.mark.parametrize("command, extracted", [
    ("echo \\\\\\<<A\nx\nA\n", False),     # three backslashes: escaped
    ("echo \\\\\\'<<A\nx\nA\n", True),     # three backslashes escape the quote
    ("echo \\'<<A\nx\nA\n", True),
])
def test_extract_heredocs_backslash_parity(command, extracted):
    assert _ph(extract_heredocs(command)) is extracted


def test_extract_heredocs_operator_at_end_of_input():
    assert extract_heredocs("cat <<'EOF'") == "cat <<'EOF'"


def test_extract_heredocs_newline_only_inside_quote_has_no_body():
    cmd = "cat <<'EOF' \"\nEOF\n"
    assert extract_heredocs(cmd, quoted_only=True) == cmd


@pytest.mark.parametrize("command", [
    "cat <<'EOF' \"\\\"\"\nbody\nEOF",        # escaped quote inside dq on the operator line
    "cat <<'EOF' \\\\\\'x\nbody\nEOF",        # three backslashes escape the quote
])
def test_extract_heredocs_operator_line_quote_tracking_extracts(command):
    assert _ph(extract_heredocs(command, quoted_only=True))


@pytest.mark.parametrize("command", [
    "cat <<'EOF' \\\\'x\nbody\nEOF",          # two backslashes: the quote opens and never closes
    "cat <<'EOF' \\''\nbody\nEOF",            # escaped quote, then a real one opens
])
def test_extract_heredocs_operator_line_quote_tracking_unchanged(command):
    assert extract_heredocs(command, quoted_only=True) == command


@pytest.mark.parametrize("quote", ["'", '"'])
def test_extract_heredocs_operator_line_multiline_quote(quote):
    cmd = f"cat <<'EOF' {quote}a\nb{quote}\nbody\nEOF"
    out = extract_heredocs(cmd, quoted_only=True)
    assert out.endswith(f"{quote}a\nb{quote}")


def test_extract_heredocs_three_trailing_backslashes_is_continuation():
    cmd = "cat <<'EOF' \\\\\\\nrm\nEOF"
    assert extract_heredocs(cmd, quoted_only=True) == cmd


def test_extract_heredocs_body_lines_with_double_spaces():
    out = extract_heredocs("cat <<'EOF'\na  b\nEOF\ntail", quoted_only=True)
    assert out.endswith("__\ntail")


@pytest.mark.parametrize("command, tail", [
    ("cat <<-'EOF'\n  EOF\nbody\nEOF\ntail", "\ntail"),     # spaces are not stripped
    ("cat <<-'EOF'\n\tXEOF\nEOF\ntail", "\ntail"),          # only tabs are stripped
])
def test_extract_heredocs_dash_strips_tabs_only(command, tail):
    out = extract_heredocs(command, quoted_only=True)
    assert out.endswith("__" + tail)


def test_extract_heredocs_operator_right_after_skipped_body():
    out = extract_heredocs("cat <<EOF\nx\nEOF\n<<'Q' cat\nbody\nQ", quoted_only=True)
    assert _ph(out)


def test_extract_heredocs_placeholder_salt_is_16_hex_chars():
    import re as _re
    out = extract_heredocs("cat <<'EOF'\nx\nEOF", quoted_only=True)
    assert _re.fullmatch(r"cat __HEREDOC_0_[0-9a-f]{16}__", out)


def test_extract_heredocs_first_operator_governs_arithmetic_check():
    assert _ph(extract_heredocs("cat <<'A'\nb\nA\n(( x = 1 << 2", quoted_only=True))


def test_extract_heredocs_backtick_at_index_zero():
    cmd = "`<<'A'\nx\nA"
    assert extract_heredocs(cmd, quoted_only=True) == cmd


# ── validateObfuscatedFlags ──────────────────────────────────────────────────

@pytest.mark.parametrize("command, behavior", [
    ("ls X-aX", "passthrough"),            # X is not a quote character
    ("ls -aXb'c'", "ask"),                 # flag keeps collecting through letters
    ("ls -a'b'", "ask"),
    ("x -'", "ask"),                       # flag at the very end of input
    (" -a'b'", "ask"),                     # whitespace at index 0
    ("ls \\x -a'b'", "ask"),               # escape does not stick
    ("ls \\\" -a'b'", "ask"),              # escaped dq does not open a quote
    ("ls ' -a\"b\"'", "passthrough"),      # single quotes hide the flag
    ("ls \"abc\" -a'b'", "ask"),           # every char inside quotes is visited
    ("ls a\"-x\"", "passthrough"),         # no whitespace before the quote
    ("ls \"abc", "passthrough"),           # unterminated quote must not raise
    ("ls \"-\"x", "ask"),                  # continuation char is the last char
    ("ls \"-.\"'x'a", "passthrough"),      # chain only for empty or dash-only prefixes
    ("ls \"-\"\".x\"", "ask"),             # chain starts at the next quote
    ("cut -f'1'", "ask"),                  # the cut exception is -d only
    ("ls -a'!", "passthrough"),            # quote then non-flag char ends the flag
    ("ls -a'B'", "ask"),                   # uppercase continues a flag
    ("ls -a'!'", "passthrough"),
    ("l -a'b'", "ask"),                    # each position is visited
    ("ls $'\" -x", "ask"),                 # empty special quotes before dash
])
def test_obfuscated_flags_mutation_cases(command, behavior):
    r = bs.validate_obfuscated_flags(ctx(command))
    assert r.behavior == behavior
    if behavior == "ask" and command != "ls $'\" -x":
        assert r.message == "Command contains quoted characters in flag names"


def test_empty_special_quotes_message():
    assert bs.validate_obfuscated_flags(ctx("ls $'\" -x")).message == (
        "Command contains empty special quotes before dash (potential bypass)"
    )


def test_quoted_flag_inside_quote_message():
    assert bs.validate_obfuscated_flags(ctx("find . \"-exec\" rm")).message == (
        "Command contains quoted characters in flag names"
    )


def test_flag_chain_extra_cases():
    assert bs._flag_in_quote_chain("X-aX", 0, "") is False
    assert bs._flag_in_quote_chain('".a"', 0, "-") is True


# ── shell-quote parse ────────────────────────────────────────────────────────

@pytest.mark.parametrize("command, tokens", [
    ("echo ${A$B}x", ["echo", "x"]),
    ("echo ${A{B}}x", ["echo", "}x"]),
    ("echo $?xy", ["echo", "y"]),
    ("a\\b\"c\"", ["abc"]),
    ("echo '$X\\'", ["echo", "$X\\"]),
    ("echo a$X", ["echo", "a"]),
])
def test_shell_quote_parse_mutation_cases(command, tokens):
    assert shell_quote_parse(command) == tokens


def test_shell_quote_parse_unclosed_brace_at_end():
    r = try_parse_shell_command("echo ${")
    assert (r.success, r.error) == (False, "Bad substitution: ")


# ── small helpers ────────────────────────────────────────────────────────────

def test_js_trim_and_ws():
    assert bs._js_trim("\ufeffx\ufeff") == "x"
    assert bs._js_trim("\x1cx") == "\x1cx"
    assert bs._is_js_ws("") is False
    assert bs._is_js_ws("\ufeff") is True


@pytest.mark.parametrize("command, parsed, expected", [
    ('"x', [], True),
    ('\\x"', [], True),
    ('"\'"', [], False),
    ("x", [{"op": ";"}, "{a"], True),
])
def test_has_malformed_tokens_mutation_cases(command, parsed, expected):
    assert has_malformed_tokens(command, parsed) is expected


@pytest.mark.parametrize("command, expected", [
    ("'\\'", True),
    ("echo \\'x\\'", False),
    ("\\x'\\'", True),
    ("\"'\\'\"", False),
    ("\"\"'\\'", True),
    ("echo '\\\\''", True),
])
def test_single_quote_bug_mutation_cases(command, expected):
    assert has_shell_quote_single_quote_bug(command) is expected


def test_has_unescaped_char_mutation_cases():
    assert has_unescaped_char("a\\", "\\") is True
    assert has_unescaped_char("\\x`", "`") is True
    with pytest.raises(ValueError, match="^hasUnescapedChar only works with single characters$"):
        has_unescaped_char("ab", "ab")


def test_backslash_helpers_at_edges():
    assert bs.has_backslash_escaped_whitespace("\\ x") is True
    assert bs.has_backslash_escaped_whitespace("a\\ ") is True
    assert bs.has_backslash_escaped_operator("\\;x") is True


# ── isSafeHeredoc / stripSafeHeredocSubstitutions ────────────────────────────

@pytest.mark.parametrize("command, expected", [
    ("echo $(cat <<'EOF'\nEOF\n)", True),               # body's first line is the delimiter
    ("echo $(cat <<'EOF'\nabc", False),                 # never closes
    ("echo $(cat <<-'EOF'\n  EOF\n)", False),           # <<- strips tabs, not spaces
    ("echo $(cat <<-'EOF'\nXEOF\n)", False),
    ("echo $(cat <<-'EOF'\n\tEOF) ;x", False),          # paren column counts the tab prefix
    ("echo $(cat <<'EOF'\nEOF);x", False),
    ("echo $(cat <<'EOF'\nEOF  ) x", True),
    ("echo $(cat <<'EOF'\nEOF;x\nEOF\n)", False),       # early-closure metachar rejects
    ("echo $(cat <<'A'\na\nA\n)$(cat <<'B'\nb\nB\n)", True),  # adjacent, not nested
    ("echo X $(cat <<'EOF'\nx\nEOF\n)", True),          # uppercase in remaining
])
def test_is_safe_heredoc_mutation_cases(command, expected):
    assert is_safe_heredoc(command) is expected


@pytest.mark.parametrize("command, expected", [
    ("$(cat <<'EOF'\nx\nEOF\n)\\", "\\"),
    ("\\$(cat <<'EOF'\nx\nEOF\n)", None),
    ("\\$(cat <<'A'\na\nA\n) $(cat <<'B'\nb\nB\n)", "\\$(cat <<'A'\na\nA\n) "),
    ("x $(cat <<'A' y\na\nA\n) $(cat <<'B'\nb\nB\n)", "x $(cat <<'A' y\na\nA\n) "),
    ("echo $(cat <<\\EOF\nx\nEOF\n)", "echo "),
    ("echo $(cat <<'EOF' \nx\nEOF\n)", "echo "),
    ("echo $(cat <<'EOF'\nEOF\n)", "echo "),
    ("echo $(cat <<-'EOF'\n  EOF\n)", None),
    ("echo $(cat <<-'EOF'\nXEOF\n)", None),
    ("echo $(cat <<'EOF'\n)\nEOF) tail", "echo  tail"),
    ("a) $(cat <<'EOF'\nEOF)", "a) "),
    ("echo $(cat <<'EOF'\nEOF) (x)", "echo  (x)"),
    ("echo $(cat <<'EOF'\nx\nEOF", None),
    ("echo $(cat <<'EOF'\n)\nEOF\n)", "echo "),
])
def test_strip_safe_heredoc_mutation_cases(command, expected):
    assert strip_safe_heredoc_substitutions(command) == expected


# ── individual validators ────────────────────────────────────────────────────

def test_jq_word_boundaries_are_ascii():
    assert bs.validate_jq_command(ctx("jq 'ésystem(1)'")).behavior == "ask"
    assert bs.validate_jq_command(ctx("jq -fé x")).behavior == "ask"


def test_shell_metacharacters_regex_branch():
    r = bs.validate_shell_metacharacters(ctx('jq -regex "a;b"x'))
    assert r.message == "Command contains shell metacharacters (;, |, or &) in arguments"


@pytest.mark.parametrize("command", ["cat < $file", "echo x | $cmd", "$cmd | cat"])
def test_dangerous_variables_lowercase(command):
    assert bs.validate_dangerous_variables(ctx(command)).behavior == "ask"


def test_zsh_lowercase_assignment_is_skipped():
    assert bs.validate_zsh_dangerous_commands(ctx("foo=bar zmodload x")).behavior == "ask"


def test_newlines_cr_only():
    assert bs.validate_newlines(ctx("ls\rid")).behavior == "ask"


@pytest.mark.parametrize("command, behavior", [
    ("\rls", "ask"),
    ("\\x\r", "ask"),
    ("'\"' \r", "ask"),
    ("\"a\" \r", "ask"),
])
def test_carriage_return_mutation_cases(command, behavior):
    assert bs.validate_carriage_return(ctx(command)).behavior == behavior


@pytest.mark.parametrize("command, message", [
    ("git commit -m 'x' --author='a > b'", "Git commit with simple quoted message is allowed"),
    ("git commit -m 'x' --author='n' > f", "Git commit remainder contains unquoted redirect operator"),
    ("git commit -m 'x' --author=\"n\" > f", "Git commit remainder contains unquoted redirect operator"),
])
def test_git_commit_remainder_quote_tracking(command, message):
    r = bs.validate_git_commit(ctx(command))
    assert (r.message or (r.decision_reason or {}).get("reason")) == message


def test_git_commit_allow_carries_command():
    r = bs.validate_git_commit(ctx("git commit -m 'x'"))
    assert r.updated_input == {"command": "git commit -m 'x'"}


@pytest.mark.parametrize("command, behavior", [
    ("echo {a {b,c}", "ask"),
    ("echo {{a},b}", "ask"),
    ("echo {,a}", "ask"),
    ("echo {\\{a\\},b}", "ask"),
    ("echo {{{a}},b}", "ask"),
    ("echo {a{b}c,d}", "ask"),
    ("echo {a{b{c}}d,e}", "ask"),
    ("echo {a..}", "ask"),
    ("echo {..}", "ask"),
])
def test_brace_expansion_mutation_cases(command, behavior):
    assert bs.validate_brace_expansion(ctx(command)).behavior == behavior


@pytest.mark.parametrize("command, behavior", [
    ("#'x", "ask"),
    ("\\x # 'y'", "ask"),
    ("'a#b' # c", "passthrough"),
    ("'a' # 'x'", "ask"),
    ("echo 'a\nb' # 'c'", "ask"),
    ("ls # x\n'y'\n", "passthrough"),
    ("a'b'# x", "passthrough"),
    ("ls #'", "ask"),
    ("ls # x'", "ask"),
    ("a'b'# x\n", "passthrough"),
    ("ls #'\n", "ask"),
])
def test_comment_quote_desync_mutation_cases(command, behavior):
    assert bs.validate_comment_quote_desync(ctx(command)).behavior == behavior


@pytest.mark.parametrize("command, behavior", [
    ("'a\n#b'", "ask"),
    ("\\x 'a\n#b'", "ask"),
    ("'a\n#", "ask"),
    ("'a\n#b\nc'", "ask"),
])
def test_quoted_newline_mutation_cases(command, behavior):
    assert bs.validate_quoted_newline(ctx(command)).behavior == behavior


def test_mid_word_hash_continuation_after_whitespace():
    assert bs.validate_mid_word_hash(ctx("echo \\\n#x")).behavior == "passthrough"
