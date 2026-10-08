"""
`app/loop/permissions/bash_commands.py`: port of claude-code's `utils/bash/commands.ts`
splitting and redirection extraction (legacy path), plus `RegexParsedCommand_DEPRECATED`.
"""

import pytest

from app.loop.permissions import bash_commands as bc
from app.loop.permissions.bash_commands import (
    ALL_SUPPORTED_CONTROL_OPERATORS,
    ALLOWED_FILE_DESCRIPTORS,
    COMMAND_LIST_SEPARATORS,
    OutputRedirection,
    extract_output_redirections,
    filter_control_operators,
    get_pipe_segments,
    is_command_list,
    is_static_redirect_target,
    is_unsafe_compound_command,
    split_command,
    split_command_with_operators,
    without_output_redirections,
)


def test_vocabularies():
    assert ALLOWED_FILE_DESCRIPTORS == frozenset({"0", "1", "2"})
    assert COMMAND_LIST_SEPARATORS == frozenset({"&&", "||", ";", ";;", "|"})
    assert ALL_SUPPORTED_CONTROL_OPERATORS == COMMAND_LIST_SEPARATORS | {">&", ">", ">>"}


# ── split_command (splitCommand_DEPRECATED) ──────────────────────────────────

@pytest.mark.parametrize("command, expected", [
    ("ls && pwd", ["ls", "pwd"]),
    ("a | b; c || d", ["a", "b", "c", "d"]),
    ("echo hi > out.txt", ["echo hi"]),
    ("echo hi 2>&1", ["echo hi"]),
    ("cmd > /dev/null 2>&1", ["cmd"]),
    ("echo x >> log.txt", ["echo x"]),
    ("echo x > $FILE", ["echo x", "$FILE"]),          # dynamic target stays visible
    ("echo x > ~/f", ["echo x", "~/f"]),
    ("cat <<'EOF'\nhi\nEOF", ["cat <<'EOF'\nhi\nEOF"]),
    ("(cd x && ls)", ["(", "cd x", "ls", ")"]),
    ("ls # comment", ["ls", "# comment"]),
    ("echo 'a && b'", ["echo 'a && b'"]),
    ("x=1 && echo $x", ["x=1", "echo $x"]),
    ("echo a\\\nb", ["echo ab"]),                      # line continuation joined
    ("ls 2> err.txt", ["ls"]),
    ("cmd >&2", ["cmd"]),
    ("ls -la | grep foo > out", ["ls -la", "grep foo"]),
    ("", []),
])
def test_split_command(command, expected):
    assert split_command(command) == expected


def test_split_command_with_operators_keeps_operators():
    parts = split_command_with_operators("ls && pwd | wc")
    assert parts == ["ls", "&&", "pwd", "|", "wc"]


def test_filter_control_operators():
    assert filter_control_operators(["a", "&&", "b", ">", "c", "|", "d", ";;", "e"]) == ["a", "b", "c", "d", "e"]


# ── is_command_list / is_unsafe_compound_command ─────────────────────────────

@pytest.mark.parametrize("command, is_list, unsafe", [
    ("ls && pwd", True, False),
    ("a | b; c || d", True, False),
    ("echo hi > out.txt", True, False),
    ("echo hi 2>&1", True, False),
    ("(cd x && ls)", False, True),        # subshell
    ("ls # comment", False, True),        # a comment
    ("a &>file", False, True),            # background &
    ("echo ${}", False, True),            # unparseable: shell-quote "Bad substitution"
    ("ls", True, False),
])
def test_command_list_and_unsafe_compound(command, is_list, unsafe):
    assert is_command_list(command) is is_list
    assert is_unsafe_compound_command(command) is unsafe


# ── extract_output_redirections ──────────────────────────────────────────────

@pytest.mark.parametrize("command, redirections, dangerous, without", [
    ("echo hi > out.txt", [("out.txt", ">")], False, "echo hi"),
    ("echo x >> log.txt", [("log.txt", ">>")], False, "echo x"),
    ("cmd > /dev/null 2>&1", [("/dev/null", ">")], False, "cmd 2>&1"),
    ("echo x > $FILE", [], True, "echo x > $FILE"),
    ("echo x > ~/f", [], True, "echo x > ~/f"),
    ("ls 2> err.txt", [("err.txt", ">")], False, "ls 2> err.txt"),
    ("echo hi >| f", [("f", ">")], False, "echo hi"),
    ("a &>file", [("file", ">")], False, "a"),
    ("ls -la | grep foo > out", [("out", ">")], False, "ls -la | grep foo"),
    ("echo 'a && b'", [], False, 'echo "a && b"'),
    ("echo hi", [], False, "echo hi"),
    ("echo ${} > x", [], True, "echo ${} > x"),  # parse failure: dangerous, original kept
])
def test_extract_output_redirections(command, redirections, dangerous, without):
    ex = extract_output_redirections(command)
    assert [(r.target, r.operator) for r in ex.redirections] == redirections
    assert ex.has_dangerous_redirection is dangerous
    assert ex.command_without_redirections == without


def test_output_redirection_dataclass():
    assert OutputRedirection(target="x", operator=">") == OutputRedirection("x", ">")


def test_heredoc_body_is_restored_after_extraction():
    ex = extract_output_redirections("cat <<'EOF' > out\nbody > x\nEOF")
    assert [(r.target, r.operator) for r in ex.redirections] == [("out", ">")]
    assert "body > x" in ex.command_without_redirections


@pytest.mark.parametrize("target, static", [
    ("out.txt", True),
    ("/dev/null", True),
    ("a b", False),
    ("'q'", False),
    ('"q"', False),
    ("$X", False),
    ("~/f", False),
])
def test_is_static_redirect_target(target, static):
    assert is_static_redirect_target(target) is static


# ── RegexParsedCommand_DEPRECATED ────────────────────────────────────────────

@pytest.mark.parametrize("command, segments", [
    ("ls -la | grep foo > out", ["ls -la", "grep foo > out"]),
    ("a | b; c || d", ["a", "b ; c || d"]),
    ("echo hi", ["echo hi"]),
    ("echo hi 2>&1", ["echo hi 2 >& 1"]),
])
def test_get_pipe_segments(command, segments):
    assert get_pipe_segments(command) == segments


def test_without_output_redirections():
    assert without_output_redirections("grep foo > out") == "grep foo"
    assert without_output_redirections("ls") == "ls"


def test_module_reexports():
    assert bc.split_command is split_command


# ── every handleRedirection / handleFileDescriptorRedirection branch ─────────

@pytest.mark.parametrize("command, redirections, dangerous, without", [
    # fd-prefixed: N>!target, N>|target, N>!word
    ("ls 2>!f", [("f", ">")], False, "ls 2> f"),
    ("ls 2>! $X", [], True, "ls 2 > ! $X"),
    ("ls 2>|f", [("f", ">")], False, "ls 2> f"),
    ("ls 2>| $X", [], True, "ls 2 > | $X"),
    ("ls 2>!$X", [], True, "ls 2 > !$X"),
    ("ls 2>!!", [], True, "ls > !!"),                    # history expansion is not a clobber target
    ("ls 2>>e.log", [("e.log", ">>")], False, "ls 2>> e.log"),
    ("ls 1>out", [("out", ">")], False, "ls"),           # stdout redirect disappears
    ("ls 2>$X", [], True, "ls > $X"),
    ("ls 1>&2", [], False, "ls 1>&2"),
    ("ls 2>&1 > out", [("out", ">")], False, "ls 2"),
    ("ls 3> f", [("f", ">")], False, "ls 3> f"),
    ("echo 2>> f", [("f", ">>")], False, "echo 2>> f"),
    # plain > with ! and | clobber forms
    ("ls >| $X", [], True, "ls > | $X"),
    ("ls >! f", [("f", ">")], False, "ls"),
    ("ls >! $X", [], True, "ls > ! $X"),
    ("ls >!f", [("f", ">")], False, "ls"),
    ("ls >!$X", [], True, "ls > !$X"),
    ("ls >!!", [], True, "ls > !!"),
    ("ls >!-1", [], True, "ls > !-1"),
    ("ls >!?x", [], True, "ls > !?x"),
    ("ls >!1", [], True, "ls > !1"),
    # &> forms
    ("ls &>! f", [("f", ">")], False, "ls"),
    ("ls &>! $X", [], True, "ls > ! $X"),
    ("ls &>| f", [("f", ">")], False, "ls"),
    ("ls &>| $X", [], True, "ls > | $X"),
    ("ls &> $X", [], True, "ls > $X"),
    # >& forms
    ("ls >& f", [("f", ">")], False, "ls"),
    ("ls >&| f", [("f", ">")], False, "ls"),
    ("ls >&| $X", [], True, "ls | $X"),
    ("ls >&! f", [("f", ">")], False, "ls"),
    ("ls >&! $X", [], True, "ls ! $X"),
    ("ls >& $X", [], True, "ls $X"),
    ("cmd >&2", [], False, "cmd 2"),                     # upstream drops a non-fd-prefixed >&
    ("ls > *.txt", [], True, "ls > *.txt"),
    ("echo x >", [], False, "echo x >"),
])
def test_redirection_branches(command, redirections, dangerous, without):
    ex = extract_output_redirections(command)
    assert [(r.target, r.operator) for r in ex.redirections] == redirections
    assert ex.has_dangerous_redirection is dangerous
    assert ex.command_without_redirections == without


@pytest.mark.parametrize("command, redirections, without", [
    ("(ls) > out", [("out", ">")], "ls"),                        # redirected subshell
    ("(ls; pwd) > out && echo", [("out", ">")], "ls ; pwd && echo"),
    ("a && (b) > out", [("out", ">")], "a && b"),
    ("x (a) > out", [("out", ">")], "x (a)"),                    # not at a command start
    ("echo $(ls > x) > y", [("y", ">")], "echo $(ls > x)"),     # redirect inside $() kept
    ("echo $(echo hi)", [], "echo $(echo hi)"),
    ('echo "$(date)"', [], "echo $(date)"),
    ("x=$(id)", [], "x=$(id)"),
    ("diff <(ls) <(pwd)", [], "diff <( ls) <( pwd)"),
    ("cat <<< 'hi' > out", [("out", ">")], "cat <<< hi"),
    ("cat << EOF > out", [("out", ">")], "cat EOF"),
    ("echo a; b > c", [("c", ">")], "echo a ; b"),
    ("echo '>' > f", [("f", ">")], "echo \\>"),
    ("echo 'a b' > f", [("f", ">")], "echo 'a b'"),
    ("echo \\( > f", [("f", ">")], "echo \\("),
    ("echo *.py > f", [("f", ">")], "echo *.py"),
    ("echo a|b > f", [("f", ">")], "echo a | b"),
    ("echo $ (x) > f", [("f", ">")], "echo $(x)"),
])
def test_reconstruction(command, redirections, without):
    ex = extract_output_redirections(command)
    assert [(r.target, r.operator) for r in ex.redirections] == redirections
    assert ex.has_dangerous_redirection is False
    assert ex.command_without_redirections == without


@pytest.mark.parametrize("xs, quoted", [
    (["a"], "a"),
    ([""], "''"),
    (["a b"], "'a b'"),
    (["it's"], '"it\'s"'),
    (['say "hi"'], "'say \"hi\"'"),
    (["$x"], "\\$x"),
    (["a*b"], "a\\*b"),
    (["C:\\x"], "'C:\\x'"),
    (["a!b"], "a\\!b"),
    (["a\tb"], "'a\tb'"),
    (["x", "y z"], "x 'y z'"),
    (["it's $x"], '"it\'s \\$x"'),
    (["C:x"], "C\\:x"),            # the drive group backtracks when no special char follows
    (["a:b"], "a\\:b"),
    ([{"op": "glob", "pattern": "*.py"}], "*.py"),
    ([{"op": "glob", "pattern": "a b*"}], "a\\ b*"),
    ([{"op": "&&"}], "\\&\\&"),
    ([{"comment": "c"}], "#c"),
])
def test_shell_quote(xs, quoted):
    assert bc.shell_quote(xs) == quoted


@pytest.mark.parametrize("xs, error", [
    ([{"op": "glob", "pattern": 1}], "glob token requires a string `pattern`"),
    ([{"op": "glob", "pattern": "a\nb"}], "glob `pattern` must not contain line terminators"),
    ([{"op": "bogus"}], "invalid `op` value: 'bogus'"),
    ([{"comment": "a\nb"}], "`comment` must not contain line terminators"),
    ([{"x": 1}], "unrecognized object token shape"),
])
def test_shell_quote_errors(xs, error):
    with pytest.raises(TypeError) as info:
        bc.shell_quote(xs)
    assert str(info.value) == error


def test_reconstruct_command_direct():
    assert bc.reconstruct_command([], "orig") == "orig"
    assert bc.reconstruct_command(["a b"], "o") == "'a b'"
    assert bc.reconstruct_command(["a|b"], "o") == '"a|b"'
    assert bc.reconstruct_command(["2>"], "o") == "2>"
    assert bc.reconstruct_command([">"], "o") == "\\>"
    assert bc.reconstruct_command(["x", {"op": "glob", "pattern": "*"}], "o") == "x *"
    assert bc.reconstruct_command(["x", {"comment": "c"}], "o") == "x"
    assert bc.reconstruct_command(["2", {"op": ">&"}, "1"], "o") == "2>&1"
    assert bc.reconstruct_command(["cat", {"op": "<"}, {"op": "<"}, "EOF"], "o") == "cat EOF"
    assert bc.reconstruct_command(["cat", {"op": "<"}, {"op": "<"}], "o") == "cat < <"
    assert bc.reconstruct_command([{"op": "&"}], "o") == "o"
    assert bc.reconstruct_command(["a", {"op": ";"}, "b", {"op": "||"}, "c", {"op": "<"}, "d"], "o") == (
        "a ; b || c < d")
    assert bc.reconstruct_command(["x$", {"op": "("}, "y", {"op": ")"}], "o") == "x$ (y)"
    assert bc.reconstruct_command(["x$", {"op": "("}, "y", {"op": ")"}, "z"], "o") == "x$(y)z"
    assert bc.reconstruct_command(["$", {"op": "("}, {"op": "("}, "y", {"op": ")"}, {"op": ")"}], "o") == "$((y))"
    assert bc.reconstruct_command(["a", {"op": "("}, "b", {"op": ")"}, "c"], "o") == "a (b)c"
    assert bc.reconstruct_command([{"op": "<("}, "ls", {"op": ")"}], "o") == "<( ls)"


@pytest.mark.parametrize("prev, kept, index, expected", [
    (None, [], 0, False),
    ("", [], 0, False),
    ("$", [], 0, True),
    ("x=$", [], 0, True),
    ("x$", ["x$", {"op": "("}, "y", {"op": ")"}, "z"], 1, True),
    ("x$", ["x$", {"op": "("}, "y", {"op": ")"}, " z"], 1, False),
    ("x$", ["x$", {"op": "("}, "y", {"op": ")"}], 1, False),
    ("x$", ["x$", {"op": "("}, {"op": "("}, {"op": ")"}, {"op": ")"}, "z"], 1, True),
    ("x", [], 0, False),
])
def test_detect_command_substitution(prev, kept, index, expected):
    assert bc._detect_command_substitution(prev, kept, index) is expected


@pytest.mark.parametrize("s, needs", [
    ("2>", False), ("10>>", False), ("a b", True), (">", True), ("(", True), ("ab", False), ("&&", False),
])
def test_needs_quoting(s, needs):
    assert bc._needs_quoting(s) is needs


@pytest.mark.parametrize("target, simple, dangerous", [
    ("f", True, False), ("", False, False), (None, False, False), ("!x", False, True), ("=x", False, True),
    ("~/x", False, True), ("$x", False, True), ("`x`", False, True), ("a*", False, True), ("a?", False, True),
    ("a[", False, True), ("a{", False, True), ("a%b", True, True), ({"op": "glob", "pattern": "*"}, False, True),
    ({"op": "|"}, False, False),
])
def test_simple_and_dangerous_targets(target, simple, dangerous):
    assert bc.is_simple_target(target) is simple
    assert bc.has_dangerous_expansion(target) is dangerous


@pytest.mark.parametrize("target", ["", "#x", "!x", "=x", "a`b", "a*", "a?", "a[", "a{", "~", "a(", "a<", "&1", "a\tb"])
def test_is_static_redirect_target_rejections(target):
    assert is_static_redirect_target(target) is False


def test_bang_target():
    assert bc._bang_target("!x") == "x"
    for t in ("!", "!!", "!-1", "!?x", "!1", "x", None):
        assert bc._bang_target(t) is None


def test_fd_redirection_helper_directly():
    kept: list[str | dict[str, str]] = ["ls", "2"]
    redirs: list = []
    assert bc._handle_fd_redirection("2", ">", "f", redirs, kept, 1) == (1, False)
    assert kept == ["ls", "2>", "f"] and redirs == [OutputRedirection("f", ">")]
    kept = ["ls", "1"]
    assert bc._handle_fd_redirection("1", ">", "f", [], kept, 2) == (2, False) and kept == ["ls"]
    kept = ["ls", "2"]
    assert bc._handle_fd_redirection("2", ">", "1", [], kept) == (1, False) and kept == ["ls", "2>", "1"]
    kept = ["ls", "1"]
    assert bc._handle_fd_redirection("1", ">", "1", [], kept) == (0, False) and kept == ["ls"]
    kept = ["ls", "2"]
    assert bc._handle_fd_redirection("2", ">", None, [], kept) == (0, False) and kept == ["ls", "2>"]
    assert bc._handle_fd_redirection("2", ">", "$x", [], []) == (0, True)


# ── splitting edge cases ─────────────────────────────────────────────────────

@pytest.mark.parametrize("command, parts", [
    ("a\nb", ["a", "b"]),
    ("echo \\(x\\)", ["echo \\(x\\)"]),
    ("ls # c 'q' \"d\"", ["ls", "# c 'q' \"d\""]),
    ("echo *.py x", ["echo *.py x"]),
    ("*.py", ["*.py"]),
    ("echo ${}", ["echo ${}"]),                       # unparseable: whole command
    ("ls\\\n-la", ["ls-la"]),
    ("a && b", ["a", "&&", "b"]),
])
def test_split_command_with_operators_edges(command, parts):
    assert split_command_with_operators(command) == parts


@pytest.mark.parametrize("command, expected", [
    ("cmd > f 2>&1", ["cmd"]),
    ("cmd 2> err > out", ["cmd"]),
    ("cmd 1> out", ["cmd"]),
    ("cmd >& 2", ["cmd"]),
    ("cmd > &2", ["cmd"]),
    ("cmd >&3", ["cmd", "3"]),
    ("cmd > a b", ["cmd", "a b"]),
    ("cmd >> a", ["cmd"]),
    ("cmd > 'a b'", ["cmd", "'a b'"]),
    ("cmd 2 > f 1 > g", ["cmd"]),
])
def test_split_command_redirection_stripping(command, expected):
    assert split_command(command) == expected


def test_is_command_list_branches():
    assert is_command_list("ls 2>&1") is True
    assert is_command_list("ls >& f") is False
    assert is_command_list("ls >> f") is True
    assert is_command_list("ls *.py") is True
    assert is_command_list("ls < f") is False


def test_get_pipe_segments_edges():
    assert get_pipe_segments("| ls") == ["ls"]
    assert get_pipe_segments("") == [""]
    assert get_pipe_segments("a | | b") == ["a", "b"]
