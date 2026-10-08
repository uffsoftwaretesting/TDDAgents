"""
Mutation-driven pins for `app/loop/permissions/bash_commands.py` and
`app/loop/permissions/bash_sed_validation.py`.

The tables below were produced by the port and checked against upstream
(`src/utils/bash/commands.ts` -> `extractOutputRedirections`, `reconstructCommand`,
`splitCommand_DEPRECATED`, `isCommandList`; `src/tools/BashTool/sedValidation.ts`).
Odd-looking rows are upstream's own behavior, kept on purpose: `reconstructCommand` only
re-emits `&& || | ; > >> <` (so a bare `&` or a non-fd `>&` drops out), suppresses the
space after a closing `)`, and the quote placeholders of `splitCommandWithOperators`
double a `"` that sits inside single quotes.
"""

import pytest

from app.loop.permissions import bash_commands as bc
from app.loop.permissions import bash_sed_validation as sv
from app.loop.permissions.bash_security import ParseEntry
from app.loop.permissions.types import PermissionBehavior, PermissionMode, ToolPermissionContext

EXTRACT = [
    ('echo a >! out', ('echo a', [('out', '>')], False)),
    ('echo a >| out', ('echo a', [('out', '>')], False)),
    ('echo a >!out', ('echo a', [('out', '>')], False)),
    ('echo a >& out', ('echo a', [('out', '>')], False)),
    ('echo a >&| out', ('echo a', [('out', '>')], False)),
    ('echo a >&! out', ('echo a', [('out', '>')], False)),
    ('echo a 2>! out', ('echo a 2> out', [('out', '>')], False)),
    ('echo a 2>| out', ('echo a 2> out', [('out', '>')], False)),
    ('echo a 2>!out', ('echo a 2> out', [('out', '>')], False)),
    ('echo a 2>&1', ('echo a 2>&1', [], False)),
    ('echo a 1>out', ('echo a', [('out', '>')], False)),
    ('echo a 2>out', ('echo a 2> out', [('out', '>')], False)),
    ('echo a > $X', ('echo a > $X', [], True)),
    ('echo a >! $X', ('echo a > ! $X', [], True)),
    ('echo a >| $X', ('echo a > | $X', [], True)),
    ('echo a 2>! $X', ('echo a 2 > ! $X', [], True)),
    ('echo a 2>| $X', ('echo a 2 > | $X', [], True)),
    ('echo a 2>!$X', ('echo a 2 > !$X', [], True)),
    ('echo a >!$X', ('echo a > !$X', [], True)),
    ('echo a >& $X', ('echo a $X', [], True)),
    ('echo a >&| $X', ('echo a | $X', [], True)),
    ('echo a >&! $X', ('echo a ! $X', [], True)),
    ('echo a > & out', ('echo a', [('out', '>')], False)),
    ('echo a > & ! out', ('echo a', [('out', '>')], False)),
    ('echo a > & | out', ('echo a', [('out', '>')], False)),
    ('echo a > & $X', ('echo a > $X', [], True)),
    ('echo a > & ! $X', ('echo a > ! $X', [], True)),
    ('echo a > & | $X', ('echo a > | $X', [], True)),
    ('echo a >> & out', ('echo a', [('out', '>>')], False)),
    ('(echo a) > out', ('echo a', [('out', '>')], False)),
    ('(echo a) >> out', ('echo a', [('out', '>>')], False)),
    ('x; (echo a) > out', ('x ; echo a', [('out', '>')], False)),
    ('x && (echo a) > o', ('x && echo a', [('o', '>')], False)),
    ('x || (echo a) > o', ('x || echo a', [('o', '>')], False)),
    ('x | (echo a) > o', ('x | echo a', [('o', '>')], False)),
    ('echo $(cat > f) > out', ('echo $(cat > f)', [('out', '>')], False)),
    ('echo a >& 2', ('echo a 2', [], False)),
    ('echo a 1>&2', ('echo a 1>&2', [], False)),
    ('echo a > out > out2', ('echo a', [('out', '>'), ('out2', '>')], False)),
    ('echo a 1>! out', ('echo a', [('out', '>')], False)),
    ('echo a 1>| out', ('echo a', [('out', '>')], False)),
    ('echo a 3> out', ('echo a 3> out', [('out', '>')], False)),
    ('echo a >!!x', ('echo a > !!x', [], True)),
    ('echo a >!-x', ('echo a > !-x', [], True)),
    ('echo a >!?x', ('echo a > !?x', [], True)),
    ('echo a >!1', ('echo a > !1', [], True)),
    ('echo a 2>!!x', ('echo a > !!x', [], True)),
    ('diff <(ls) <(ls) > out', ('diff <( ls) <( ls)', [('out', '>')], False)),
    ('echo $((1+2)) > o', ('echo $((1+2))', [('o', '>')], False)),
    ('a=$(b) > o', ('a=$(b)', [('o', '>')], False)),
    ('cat <<EOF > o\nx\nEOF', ('cat <<EOF\nx\nEOF', [('o', '>')], False)),
    ('echo a >> o', ('echo a', [('o', '>>')], False)),
    ("echo 'q' > o", ('echo q', [('o', '>')], False)),
    ('echo "d q" > o', ("echo 'd q'", [('o', '>')], False)),
    ('echo a > o; ls', ('echo a ; ls', [('o', '>')], False)),
    ('echo $( (a) ) > o', ('echo $((a))', [('o', '>')], False)),
    ('echo a 2>&1 >o', ('echo a 2', [('o', '>')], False)),
    ('ls ( a ) > o', ('ls (a)', [('o', '>')], False)),
    ('echo `x` > o', ('echo `x`', [('o', '>')], False)),
    ('echo a|b > o', ('echo a | b', [('o', '>')], False)),
    ('echo a > o &', ('echo a', [('o', '>')], False)),
    ('echo a &> o', ('echo a', [('o', '>')], False)),
    ('foo (a) > o', ('foo (a)', [('o', '>')], False)),
    ('x;(a)>o', ('x ; a', [('o', '>')], False)),
    ('echo $(a) $(b) > o', ('echo $(a)$(b)', [('o', '>')], False)),
    ('echo $(a (b)) > o', ('echo $(a(b))', [('o', '>')], False)),
    ('cat << EOF > o\nx\nEOF', ('cat << EOF\nx\nEOF', [('o', '>')], False)),
    ('echo a 2>! out x', ('echo a 2> out x', [('out', '>')], False)),
    ('echo a > & ! out x', ('echo a x', [('out', '>')], False)),
    ('echo a 2>| out x', ('echo a 2> out x', [('out', '>')], False)),
    ('echo a > ! out', ('echo a', [('out', '>')], False)),
    ('echo a 2 >& 1', ('echo a 2>&1', [], False)),
    ('echo a <<< s > o', ('echo a <<< s', [('o', '>')], False)),
    ('echo a > ~/x', ('echo a > ~/x', [], True)),
    ('echo a > x*', ('echo a > x*', [], True)),
    ('echo a 2> $X', ('echo a > $X', [], True)),
    ('echo a >& !x', ('echo a !x', [], True)),
    ('echo a >& !$X', ('echo a !$X', [], True)),
    ('echo a 1>&2 > o', ('echo a 1 2> o', [('o', '>')], False)),
]

SPLIT = [
    ('echo a 2>&1 | grep b', ['echo a', 'grep b']),
    ('echo a > /dev/null', ['echo a']),
    ('echo a >&2', ['echo a']),
    ('echo a > &2', ['echo a']),
    ('echo a 2> err > out', ['echo a']),
    ('ls 1> out 2> err', ['ls']),
    ('echo hi >> log', ['echo hi']),
    ('echo > $F', ['echo', '$F']),
    ('cmd > out 2>&1', ['cmd']),
    ('a\\\nb', ['ab']),
    ('echo "x\ny"', ['echo "x\ny"']),
    ('echo a > & 2', ['echo a']),
    ('echo a >& 1 && b', ['echo a', 'b']),
    ('ls\nls', ['ls', 'ls']),
    ('echo a 2 > out', ['echo a']),
    ('echo ab 2 > out', ['echo ab']),
    ('echo a >> out 2 > err', ['echo a']),
    ('cmd > o 2> e', ['cmd']),
    ('x 2>&1', ['x']),
    ('echo a > out2', ['echo a']),
    ('echo a >& 3', ['echo a', '3']),
    ('echo a > &3', ['echo a', '&', '3']),
    ('cmd >o', ['cmd']),
    ('echo \'a"b\' > o', ['echo \'a""b\'']),
    ('echo "it\'s" > o', ['echo "it\'\'s"']),
    ('cat <<EOF\nx\nEOF', ['cat <<EOF\nx\nEOF']),
    ('a > b c', ['a', 'b c']),
    ('a 2 > out', ['a']),
    ('> out ls 2', ['out ls 2']),
    ('echo a >', ['echo a']),
    ('echo a >>', ['echo a']),
    ('echo a > o 2', ['echo a', 'o 2']),
    ('echo a > o 2 >> e', ['echo a']),
    ('echo a >> o 1 > e', ['echo a']),
    ('echo a > & 1 x', ['echo a', '&', '1 x']),
    ('echo a 2 >& 1', ['echo a']),
    ('a 2 >& 1', ['a']),
    ('echo \\( x \\)', ['echo \\( x \\)']),
    ('echo # c', ['echo', '# c']),
    ('echo *.py > o', ['echo *.py']),
    ('echo a 2> &1', ['echo a']),
]

IS_LIST = [
    ('a && b', True),
    ('a; b', True),
    ('a | b', True),
    ('(a)', False),
    ('a > f', True),
    ('a >& 2', True),
    ('a >& f', False),
    ('a # c', False),
    ('a *.py', True),
    ('a < f', False),
    ('a >> f', True),
    ('a ;; b', True),
    ('a 2>& 1', True),
    ('a >& 1 b', True),
    ('a >&', False),
    ('a >& "2"', False),
    ('a $X > f', True),
    ('a "x" && b', True),
    ("a 'y' ; b", True),
    ('a >& 0', True),
]

UNSAFE = [
    ('a && b', False),
    ('(a)', True),
    ('a | b', False),
    ('a', False),
    ('a > f && b', False),
    ('{ a; }', False),
    ('a "unclosed', False),
    ('a $(b)', True),
    ('a < f', True),
    ('a; (b)', True),
]

JOIN = [
    ('a\\\nb', 'ab'),
    ('a\\\\\nb', 'a\\\\\nb'),
    ('a\\\\\\\nb', 'a\\\\b'),
    ('a\\\\\\\\\nb', 'a\\\\\\\\\nb'),
    ('x\\\ny\\\nz', 'xyz'),
    ('\\\n', ''),
]

QUOTE = [
    (['a b'], "'a b'"),
    (["it's"], '"it\'s"'),
    (['a\\b'], "'a\\b'"),
    (['$x'], '\\$x'),
    (['a:b'], 'a\\:b'),
    (['C:x'], 'C\\:x'),
    (['c:#'], 'c:\\#'),
    ([''], "''"),
    ([{'op': 'glob', 'pattern': '*.py'}], '*.py'),
    ([{'op': '&&'}], '\\&\\&'),
    (['a"b'], '\'a"b\''),
    (['a', 'b'], 'a b'),
    (['x\ty'], "'x\ty'"),
    (['Zz:a'], 'Zz\\:a'),
]

NEEDS = [
    ('2>', False),
    ('2>>', False),
    ('12>', False),
    (' ', True),
    ('>', True),
    ('<', True),
    ('a', False),
    ('ab', False),
    ('|', True),
    ('&', True),
    (';', True),
    ('(', True),
    (')', True),
    ('x', False),
    ('a b', True),
    ('XX', False),
    ('2>>>', False),
]

PIPES = [
    ('a | b', ['a', 'b']),
    ('a|b|c', ['a', 'b', 'c']),
    ('| a', ['a']),
    ('a', ['a']),
    ('a || b', ['a || b']),
    ('', ['']),
    ('a | | b', ['a', 'b']),
]

WITHOUT = [
    ('echo a', 'echo a'),
    ('echo a > o', 'echo a'),
    ('echo a 2>&1', 'echo a 2>&1'),
    ('echo > $X', 'echo > $X'),
    ('a >& 2', 'a >& 2'),
]


@pytest.mark.parametrize("command, expected", EXTRACT)
def test_extract_output_redirections_table(command, expected):
    e = bc.extract_output_redirections(command)
    got = (
        e.command_without_redirections,
        [(r.target, r.operator) for r in e.redirections],
        e.has_dangerous_redirection,
    )
    assert got == expected


@pytest.mark.parametrize("command, expected", SPLIT)
def test_split_command_table(command, expected):
    assert bc.split_command(command) == expected


@pytest.mark.parametrize("command, expected", IS_LIST)
def test_is_command_list_table(command, expected):
    assert bc.is_command_list(command) is expected


@pytest.mark.parametrize("command, expected", UNSAFE)
def test_is_unsafe_compound_command_table(command, expected):
    assert bc.is_unsafe_compound_command(command) is expected


@pytest.mark.parametrize("text, expected", JOIN)
def test_join_continuations_table(text, expected):
    assert bc._join_continuations(text) == expected


@pytest.mark.parametrize("tokens, expected", QUOTE)
def test_shell_quote_table(tokens, expected):
    assert bc.shell_quote(tokens) == expected


@pytest.mark.parametrize("token, expected", NEEDS)
def test_needs_quoting_table(token, expected):
    assert bc._needs_quoting(token) is expected


@pytest.mark.parametrize("command, expected", PIPES)
def test_get_pipe_segments_table(command, expected):
    assert bc.get_pipe_segments(command) == expected


@pytest.mark.parametrize("command, expected", WITHOUT)
def test_without_output_redirections_table(command, expected):
    assert bc.without_output_redirections(command) == expected


def test_placeholders_are_salted_per_call():
    a, b = bc._placeholders(), bc._placeholders()
    assert a["DOUBLE_QUOTE"] != b["DOUBLE_QUOTE"]
    salt = a["NEW_LINE"][len("__NEW_LINE_"):-2]
    assert len(salt) == 16 and all(ch in "0123456789abcdef" for ch in salt)


def test_newline_placeholder_is_removed_from_split_output():
    assert bc.split_command_with_operators("a\nb") == ["a", "b"]
    assert bc.split_command_with_operators('echo "x\ny"') == ['echo "x\ny"']


@pytest.mark.parametrize("prev, kept, index, expected", [
    ("$", [], 0, True),
    ("a=$", [], 0, True),
    ("x$", ["x$", {"op": "("}, "y", {"op": ")"}, "z"], 1, True),
    ("x$", ["x$", {"op": "("}, "y", {"op": ")"}, " z"], 1, False),
    ("x$", ["x$", {"op": "("}, {"op": "("}, {"op": ")"}, {"op": ")"}, "z"], 1, True),
    ("x$", ["x$", {"op": "("}, {"op": "("}, {"op": ")"}, "z"], 1, False),
    ("x$", ["x$", {"op": "("}, "y"], 1, False),
    ("x$", ["x$", {"op": "("}, {"op": ")"}], 1, False),
    ("x", [], 0, False),
    ("", [], 0, False),
    (None, [], 0, False),
    ("=x$", [], 0, False),
    ("a=x$", [], 0, False),
])
def test_detect_command_substitution(prev, kept, index, expected):
    assert bc._detect_command_substitution(prev, kept, index) is expected


def test_reconstruct_command_edges():
    assert bc.reconstruct_command([], "orig") == "orig"
    assert bc.reconstruct_command(["a", {"op": "<"}, {"op": "<"}, "EOF", "b"], "x") == "a EOF b"
    # no delimiter: upstream falls through and re-emits both `<`
    assert bc.reconstruct_command(["a", {"op": "<"}, {"op": "<"}], "x") == "a < <"
    assert bc.reconstruct_command(["a", {"op": "<"}, "f"], "x") == "a < f"
    assert bc.reconstruct_command(["a", {"op": ">>"}, "f"], "x") == "a >> f"
    assert bc.reconstruct_command(["a", "2", {"op": ">&"}, "1", "b"], "x") == "a 2>&1 b"
    assert bc.reconstruct_command(["x", "2", "y", "2", {"op": ">&"}, "1"], "o") == "x 2 y 2>&1"
    assert bc.reconstruct_command(["a", {"op": "<("}, "b", {"op": ")"}, "c"], "x") == "a <( b)c"
    assert bc.reconstruct_command(["a$", {"op": "("}, {"op": "("}, "b", {"op": ")"}, {"op": ")"}], "x") == "a$ ((b))"
    two_groups: list[ParseEntry] = ["$", {"op": "("}, "b", {"op": ")"}, {"op": "("}, "c", {"op": ")"}]
    assert bc.reconstruct_command(two_groups, "x") == "$(b) (c)"
    assert bc.reconstruct_command(["a ", {"op": "("}], "x") == "'a ' ("
    assert bc.reconstruct_command(["a|b"], "x") == '"a|b"'
    assert bc.reconstruct_command([{"op": "glob", "pattern": "*.py"}, "x"], "o") == "*.py x"
    assert bc.reconstruct_command([{"comment": "c"}, "x"], "o") == "x"
    # JS trim() keeps \x1c, which Python's str.strip() would remove
    assert bc.reconstruct_command([{"op": "glob", "pattern": "a\x1c"}], "orig") == "a\x1c"
    assert bc.reconstruct_command([{"op": "&"}], "orig") == "orig"


def test_handle_fd_redirection_default_skip_is_one():
    kept: list[ParseEntry] = ["ls", "1"]
    assert bc._handle_fd_redirection("1", ">", "f", [], kept) == (1, False)


def test_has_dangerous_expansion_and_simple_target_types():
    assert bc.has_dangerous_expansion(None) is False
    assert bc.has_dangerous_expansion("") is False
    assert bc.has_dangerous_expansion({"op": "glob", "pattern": "*"}) is True
    assert bc.has_dangerous_expansion({"op": ">"}) is False
    assert bc.has_dangerous_expansion("a%b") is True
    assert bc.is_simple_target("a%b") is True


# ── sed ──────────────────────────────────────────────────────────────────────

def test_string_flags_drop_double_dash():
    assert sv._string_flags("-n -- 1p f") == ["-n"]
    assert sv.is_line_printing_command("sed -n -- 1p f", ["1p"]) is True


def test_combined_short_flags_check_every_letter():
    assert sv.validate_flags_against_allowlist(["-xn"], sv.LINE_PRINTING_FLAGS) is False
    assert sv.validate_flags_against_allowlist(["-nE"], sv.LINE_PRINTING_FLAGS) is True


@pytest.mark.parametrize("command, expected", [
    ("sed --silent 1p f", True),
    ("sed --quiet 1p f", True),
    ("sed -E 1p f", False),
    ("sed --zero-terminated 1p f", False),
    ("sed -nE 1p f", True),
])
def test_line_printing_needs_a_quiet_flag(command, expected):
    assert sv.is_line_printing_command(command, ["1p"]) is expected


def test_line_printing_trims_js_whitespace_only():
    assert sv.is_line_printing_command("sed -n 1p f", ["1p﻿"]) is True


def test_substitution_edges():
    assert sv.is_substitution_command("sed ${} s/a/b/", ["s/a/b/"], False) is False
    assert sv.is_substitution_command("sed s/a/b/", ["﻿s/a/b/"], False) is True
    assert sv.is_substitution_command("sed s//x/", ["s//x/"], False) is True
    assert sv.is_substitution_command("sed x", ["s/\\a/b/"], False) is True
    assert sv.is_substitution_command("sed x", ["s/a/b/g2"], False) is True
    assert sv.is_substitution_command("sed x", ["s/a/b/w"], False) is False


@pytest.mark.parametrize("command, expected", [
    ("sed 1p > out", True),
    ("sed -e p -e q", False),
    ("sed --expression p --expression q", False),
    ("sed f -e p", False),  # the operand precedes -e, so it is the script position
    ("sed ${} p", True),
    ("sed -n p f", True),
    ("sed -n p", False),
    ("sed p", False),
    ("sed *.txt p", True),
])
def test_has_file_args(command, expected):
    assert sv.has_file_args(command) is expected


def test_extract_sed_expressions_edges():
    with pytest.raises(sv.SedParseError, match=r"^Dangerous flag combination detected$"):
        sv.extract_sed_expressions("sed -ew x")
    assert sv.extract_sed_expressions("sed > out p") == ["out"]
    assert sv.extract_sed_expressions("sed --expression p --expression q") == ["p", "q"]
    assert sv.extract_sed_expressions("sed -e") == []
    assert sv.extract_sed_expressions("sed -e p -e q") == ["p", "q"]
    assert sv.extract_sed_expressions("sed --expression=p --expression=q") == ["p", "q"]
    assert sv.extract_sed_expressions("sed -e=p -e=q") == ["p", "q"]
    assert sv.extract_sed_expressions("sed -n p") == ["p"]
    assert sv.extract_sed_expressions("sed -n -E p f") == ["p"]
    # an -e after the positional script still contributes (upstream loop does not break on it)
    assert sv.extract_sed_expressions("sed -n p -e q") == ["p", "q"]
    assert sv.extract_sed_expressions("sed p -e q") == ["p", "q"]


@pytest.mark.parametrize("expr, expected", [
    ("p﻿", False),
    ("1{p", True),
    ("1}p", True),
    ("#s", True),
    ("1,~2p", True),
    ("1\\/w", True),
    ("1\\/W", True),
    ("/x/ W", True),
    ("sxw", True),
    ("sxW", True),
    ("1s|a|b|e", True),
    ("1s|a|b|E", True),
    ("1s|a|b|X", False),
    ("y/a/b/W", True),
])
def test_contains_dangerous_operations_edges(expr, expected):
    assert sv.contains_dangerous_operations(expr) is expected


def test_substitution_with_semicolon_is_not_allowed():
    assert sv.sed_command_is_allowed_by_allowlist("sed 's/a;b/c/'") is False
    assert sv.sed_command_is_allowed_by_allowlist("sed 's/a/c/'") is True


def test_check_sed_constraints_trims_js_whitespace():
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT)
    res = sv.check_sed_constraints("﻿sed -n 1w f", ctx)
    assert res.behavior == PermissionBehavior.ASK
