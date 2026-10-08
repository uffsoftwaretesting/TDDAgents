"""
`app/loop/permissions/bash_sed_validation.py`: port of claude-code's
`tools/BashTool/sedValidation.ts`.
"""

import pytest

from app.loop.permissions.bash_sed_validation import (
    IN_PLACE_FLAGS,
    LINE_PRINTING_FLAGS,
    SED_ASK_MESSAGE,
    SED_ASK_REASON,
    SUBSTITUTION_FLAGS,
    SedParseError,
    check_sed_constraints,
    contains_dangerous_operations,
    extract_sed_expressions,
    has_file_args,
    is_line_printing_command,
    is_print_command,
    is_substitution_command,
    sed_command_is_allowed_by_allowlist,
    validate_flags_against_allowlist,
)
from app.loop.permissions.types import PermissionMode, ToolPermissionContext


def test_flag_vocabularies():
    assert LINE_PRINTING_FLAGS == (
        "-n", "--quiet", "--silent", "-E", "--regexp-extended", "-r", "-z", "--zero-terminated", "--posix")
    assert SUBSTITUTION_FLAGS == ("-E", "--regexp-extended", "-r", "--posix")
    assert IN_PLACE_FLAGS == ("-i", "--in-place")


@pytest.mark.parametrize("flags, ok", [
    (["-n"], True), (["-nE"], True), (["-nX"], False), (["--quiet"], True), (["--bogus"], False),
    (["-E", "-r"], True), ([], True), (["-ab"], False),
])
def test_validate_flags_against_allowlist(flags, ok):
    assert validate_flags_against_allowlist(flags, LINE_PRINTING_FLAGS) is ok


@pytest.mark.parametrize("cmd, ok", [
    ("p", True), ("1p", True), ("123p", True), ("1,5p", True), ("10,200p", True),
    ("", False), ("w x", False), ("1,p", False), ("p1", False), ("1d", False), ("1,5pp", False), ("e", False),
])
def test_is_print_command(cmd, ok):
    assert is_print_command(cmd) is ok


@pytest.mark.parametrize("command, ok", [
    ("sed -n '1,5p' file.txt", True),
    ("sed -n '1p;2p;3p' f", True),
    ("sed --quiet 'p'", True),
    ("sed -nE '1p'", True),
    ("sed -En 2p", True),
    ("sed '1,5p' f", False),                # no -n
    ("sed -n '1,5d' f", False),             # not a print command
    ("sed -n -i '1p' f", False),            # -i not allowed
    ("sed -n", False),                      # no expression
    ("echo -n '1p'", False),                # not sed
    ("sed -n '1p' ${}", False),             # unparseable
])
def test_is_line_printing_command(command, ok):
    try:
        expressions = extract_sed_expressions(command)
    except SedParseError:
        expressions = []
    assert is_line_printing_command(command, expressions) is ok


@pytest.mark.parametrize("command, has_files, writes, ok", [
    ("sed 's/a/b/'", False, False, True),
    ("sed 's/a/b/g'", False, False, True),
    ("sed 's/a/b/gpI2'", False, False, True),
    ("sed 's/a/b/22'", False, False, False),     # two digits
    ("sed 's/a/b/w'", False, False, False),
    ("sed 's/a\\/x/b/'", False, False, True),    # escaped delimiter
    ("sed 's/a/b'", False, False, False),        # one delimiter
    ("sed 's/a/b/c/d'", False, False, False),    # three delimiters
    ("sed 's|a|b|'", False, False, False),       # only / is allowed
    ("sed 'y/a/b/'", False, False, False),
    ("sed -E 's/a/b/'", False, False, True),
    ("sed -i 's/a/b/' f", True, False, False),   # files without writes
    ("sed -i 's/a/b/' f", True, True, True),     # acceptEdits
    ("sed --in-place 's/a/b/' f", True, True, True),
    ("sed -n 's/a/b/'", False, False, False),    # -n is not a substitution flag
    ("echo 's/a/b/'", False, False, False),
])
def test_is_substitution_command(command, has_files, writes, ok):
    expressions = extract_sed_expressions(command)
    assert is_substitution_command(command, expressions, has_files, allow_file_writes=writes) is ok


def test_substitution_requires_exactly_one_expression_and_no_line_terminator():
    assert is_substitution_command("sed -e 's/a/b/' -e 's/c/d/'", ["s/a/b/", "s/c/d/"], True,
                                   allow_file_writes=True) is False
    assert is_substitution_command("sed 's/a\nb/c/'", ["s/a\nb/c/"], False) is False
    assert is_substitution_command("sed ${}", ["s/a/b/"], False) is False


@pytest.mark.parametrize("command, files", [
    ("sed 's/a/b/'", False),
    ("sed 's/a/b/' f", True),
    ("sed -e 's/a/b/' f", True),
    ("sed -e 's/a/b/'", False),
    ("sed --expression='s/a/b/' f", True),
    ("sed -e='s/a/b/' f", True),
    ("sed 's/a/b/' *.log", True),             # glob counts as a file
    ("sed -n '1p'", False),
    ("echo x", False),
    ("sed ${}", True),                        # unparseable: assume files
    ("sed -E 's/a/b/'", False),
])
def test_has_file_args(command, files):
    assert has_file_args(command) is files


@pytest.mark.parametrize("command, expressions", [
    ("sed 's/a/b/' f", ["s/a/b/"]),
    ("sed -e 's/a/b/' -e 's/c/d/' f", ["s/a/b/", "s/c/d/"]),
    ("sed --expression='1p' f", ["1p"]),
    ("sed -e='1p' f", ["1p"]),
    ("sed -n -E '1p' f g", ["1p"]),
    ("echo x", []),
    ("sed", []),
])
def test_extract_sed_expressions(command, expressions):
    assert extract_sed_expressions(command) == expressions


@pytest.mark.parametrize("command", ["sed -ew x f", "sed -eW x", "sed -ee x", "sed -we x", "sed -wE x"])
def test_extract_rejects_dangerous_flag_combinations(command):
    with pytest.raises(SedParseError, match="Dangerous flag combination detected"):
        extract_sed_expressions(command)


def test_extract_rejects_malformed():
    with pytest.raises(SedParseError, match="Malformed shell syntax: Bad substitution"):
        extract_sed_expressions("sed ${}")


@pytest.mark.parametrize("expr, dangerous", [
    ("s/a/b/", False),
    ("1,5p", False),
    ("", False),
    ("s/ａ/b/", True),          # non-ASCII
    ("{p}", True),
    ("1p\n2p", True),
    ("#comment", True),
    ("s#a#b#", False),          # # as delimiter after s
    ("!p", True),
    ("/x/!d", True),
    ("1!d", True),
    ("$!d", True),
    ("1~2p", True),
    (",~2p", True),
    ("$~2p", True),
    (",5p", True),
    ("1,+2p", True),
    ("1,-2p", True),
    ("s\\a\\b\\", True),
    ("s/a\\|b/c/", True),
    ("/\\/etc\\/x/w out", True),
    ("/foo w file", True),
    ("s/a/bc", True),           # malformed s/
    ("s/a/b//w", True),
    ("s|a|b|w", True),          # non-slash delimiter, dangerous flag
    ("s|a|b|g", False),
    ("w out", True),
    ("1w out", True),
    ("$ w out", True),
    ("/x/w out", True),
    ("1,10w out", True),
    ("1,$w out", True),
    ("/a/,/b/w out", True),
    ("e ls", True),
    ("1e", True),
    ("$e", True),
    ("/x/e", True),
    ("1,10e", True),
    ("1,$e", True),
    ("/a/,/b/e", True),
    ("s/a/b/gw", True),
    ("s/a/b/e", True),
    ("s:a:b:W", True),
    ("y/abc/xyz/", False),
    ("y/abc/xyw/", True),       # y with any w/e anywhere
    ("p", False),
])
def test_contains_dangerous_operations(expr, dangerous):
    assert contains_dangerous_operations(expr) is dangerous


@pytest.mark.parametrize("command, writes, ok", [
    ("sed -n '1,5p' file.txt", False, True),
    ("sed 's/a/b/'", False, True),
    ("sed 's/a/b/' f", False, False),         # substitution to a file without acceptEdits
    ("sed -i 's/a/b/' f", True, True),
    ("sed -n '1,5p' f", True, False),         # write mode only checks substitution
    ("sed 's/a/b/;s/c/d/'", False, False),    # ; in a substitution
    ("sed -n '1p;w out' f", False, False),
    ("sed 's/a/b/w out'", False, False),
    ("sed -ew out f", False, False),          # parse error -> not allowed
    ("sed 's/ａ/b/'", False, False),          # denylist after allowlist
])
def test_sed_command_is_allowed_by_allowlist(command, writes, ok):
    assert sed_command_is_allowed_by_allowlist(command, allow_file_writes=writes) is ok


def test_check_sed_constraints():
    ctx = ToolPermissionContext()
    res = check_sed_constraints("ls && sed 's/a/b/w out' f", ctx)
    assert res.behavior == "ask"
    assert res.message == SED_ASK_MESSAGE
    assert res.decision_reason == {"type": "other", "reason": SED_ASK_REASON}
    ok = check_sed_constraints("ls && sed -n '1p' f", ctx)
    assert (ok.behavior, ok.message) == ("passthrough", "No dangerous sed operations detected")
    assert check_sed_constraints("echo sed", ctx).behavior == "passthrough"
    edits = ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS)
    assert check_sed_constraints("sed -i 's/a/b/' f", edits).behavior == "passthrough"
    assert check_sed_constraints("sed -i 's/a/b/' f", ctx).behavior == "ask"


def test_messages():
    assert SED_ASK_MESSAGE == "sed command requires approval (contains potentially dangerous operations)"
    assert SED_ASK_REASON == (
        "sed command contains operations that require explicit approval (e.g., write commands, execute commands)")
