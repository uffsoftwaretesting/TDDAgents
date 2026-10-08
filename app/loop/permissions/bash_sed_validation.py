"""
sed command validation for the Bash tool.

Ported from claude-code v2.1.88 `src/tools/BashTool/sedValidation.ts`:
`validateFlagsAgainstAllowlist`, `isLinePrintingCommand`, `isPrintCommand`,
`isSubstitutionCommand`, `sedCommandIsAllowedByAllowlist`, `hasFileArgs`,
`extractSedExpressions`, `containsDangerousOperations`, `checkSedConstraints`.

Two patterns are allowed, nothing else: line printing (`sed -n '1,5p'`, file arguments
allowed) and a single `s/pattern/replacement/flags` substitution to stdout (in
acceptEdits mode, also in place with `-i`). A denylist then rejects `w`/`W`/`e`/`E`
commands and flags, blocks, comments, negation, GNU step/offset addresses and backslash
delimiter tricks, as defense in depth.

JavaScript regex semantics are written out: `\\s`/`\\S` are the JS whitespace classes,
`\\d` is ASCII, `.` excludes JS line terminators and `$` (no `m` flag) is `\\Z`.

`sedEditParser.ts` is not ported: it parses `sed -i` edits for upstream's edit-preview
UI, which this loop does not have; `checkSedConstraints` does not use it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.loop.permissions.bash_commands import split_command
from app.loop.permissions.bash_security import try_parse_shell_command
from app.loop.permissions.types import PermissionBehavior, PermissionMode, PermissionResult

if TYPE_CHECKING:
    from app.loop.permissions.types import ToolPermissionContext

_WS_CHARS = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + _WS_CHARS + "]"
_NS = "[^" + _WS_CHARS + "]"
_DOT = "[^\n\r  ]"
_JS_TRIM = "\t\n\x0b\x0c\r              " \
    "    　﻿"

_SED_PREFIX = re.compile("^" + _S + "*sed" + _S + "+")

LINE_PRINTING_FLAGS: tuple[str, ...] = (
    "-n", "--quiet", "--silent", "-E", "--regexp-extended", "-r", "-z", "--zero-terminated", "--posix",
)
SUBSTITUTION_FLAGS: tuple[str, ...] = ("-E", "--regexp-extended", "-r", "--posix")
IN_PLACE_FLAGS: tuple[str, ...] = ("-i", "--in-place")

SED_ASK_MESSAGE = "sed command requires approval (contains potentially dangerous operations)"
SED_ASK_REASON = (
    "sed command contains operations that require explicit approval (e.g., write commands, execute commands)"
)


class SedParseError(ValueError):
    """`extractSedExpressions` throws; the allowlist treats that as not allowed."""


def _without_sed(command: str) -> str | None:
    m = _SED_PREFIX.match(command)
    return command[m.end():] if m else None


def _string_flags(without_sed: str) -> list[str] | None:
    parsed = try_parse_shell_command(without_sed)
    if not parsed.success:
        return None
    return [a for a in parsed.tokens if isinstance(a, str) and a.startswith("-") and a != "--"]


def validate_flags_against_allowlist(flags: list[str], allowed: tuple[str, ...]) -> bool:
    """`validateFlagsAgainstAllowlist`: combined short flags are checked letter by letter."""
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 2:
            if any("-" + ch not in allowed for ch in flag[1:]):
                return False
        elif flag not in allowed:
            return False
    return True


def is_print_command(cmd: str) -> bool:
    """`isPrintCommand`: exactly `p`, `Np` or `N,Mp`."""
    return bool(cmd) and re.fullmatch(r"(?:[0-9]+|[0-9]+,[0-9]+)?p", cmd) is not None


def is_line_printing_command(command: str, expressions: list[str]) -> bool:
    """`isLinePrintingCommand`: `-n` plus print commands only; file arguments allowed."""
    without = _without_sed(command)
    if without is None:
        return False
    flags = _string_flags(without)
    if flags is None or not validate_flags_against_allowlist(flags, LINE_PRINTING_FLAGS):
        return False
    has_n = any(
        f in ("-n", "--quiet", "--silent") or (f.startswith("-") and not f.startswith("--") and "n" in f)
        for f in flags
    )
    if not has_n or not expressions:
        return False
    return all(is_print_command(cmd.strip(_JS_TRIM)) for expr in expressions for cmd in expr.split(";"))


def is_substitution_command(
    command: str, expressions: list[str], has_file_arguments: bool, *, allow_file_writes: bool = False
) -> bool:
    """`isSubstitutionCommand`: one `s/a/b/flags` with flags from `gpimIM` and one digit."""
    if not allow_file_writes and has_file_arguments:
        return False
    without = _without_sed(command)
    if without is None:
        return False
    flags = _string_flags(without)
    if flags is None:
        return False
    allowed = SUBSTITUTION_FLAGS + (IN_PLACE_FLAGS if allow_file_writes else ())
    if not validate_flags_against_allowlist(flags, allowed):
        return False
    if len(expressions) != 1:
        return False
    expr = expressions[0].strip(_JS_TRIM)
    # `/^s\/(.*?)$/`: JS `.` cannot cross a line terminator, so one anywhere fails the match.
    if not expr.startswith("s/") or re.search("[\n\r\u2028\u2029]", expr):
        return False
    rest = expr[2:]
    delimiters = 0
    last = -1
    i = 0
    while i < len(rest):
        if rest[i] == "\\":
            i += 2
            continue
        if rest[i] == "/":
            delimiters += 1
            last = i
        i += 1
    if delimiters != 2:
        return False
    return re.fullmatch(r"[gpimIM]*[1-9]?[gpimIM]*", rest[last + 1:]) is not None


def has_file_args(command: str) -> bool:
    """`hasFileArgs`: any glob, any operand after `-e`, or a second operand."""
    without = _without_sed(command)
    if without is None:
        return False
    parsed = try_parse_shell_command(without)
    if not parsed.success:
        return True
    tokens = parsed.tokens
    arg_count = 0
    has_e = False
    i = 0
    while i < len(tokens):
        arg = tokens[i]
        if isinstance(arg, dict):
            if arg.get("op") == "glob":
                return True
            i += 1
            continue
        if arg in ("-e", "--expression") and i + 1 < len(tokens):
            has_e = True
            i += 2
            continue
        if arg.startswith("--expression=") or arg.startswith("-e="):
            has_e = True
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue
        arg_count += 1
        if has_e or arg_count > 1:
            return True
        i += 1
    return False


def extract_sed_expressions(command: str) -> list[str]:
    """`extractSedExpressions`: the script(s), never the file names. Raises on danger/malformed."""
    without = _without_sed(command)
    if without is None:
        return []
    if re.search("-e[wWe]", without) or re.search("-w[eE]", without):
        raise SedParseError("Dangerous flag combination detected")
    parsed = try_parse_shell_command(without)
    if not parsed.success:
        raise SedParseError(f"Malformed shell syntax: {parsed.error}")
    tokens = parsed.tokens
    expressions: list[str] = []
    found_e = False
    found_expression = False
    i = 0
    while i < len(tokens):
        arg = tokens[i]
        if not isinstance(arg, str):
            i += 1
            continue
        if arg in ("-e", "--expression") and i + 1 < len(tokens):
            found_e = True
            nxt = tokens[i + 1]
            if isinstance(nxt, str):
                expressions.append(nxt)
                i += 1
            i += 1
            continue
        if arg.startswith("--expression="):
            found_e = True
            expressions.append(arg[len("--expression="):])
            i += 1
            continue
        if arg.startswith("-e="):
            found_e = True
            expressions.append(arg[len("-e="):])
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue
        if not found_e and not found_expression:
            expressions.append(arg)
            found_expression = True
            i += 1
            continue
        break
    return expressions


_WRITE_COMMAND_PATTERNS = tuple(re.compile(p) for p in (
    "^[wW]" + _S + "*" + _NS + "+",
    "^[0-9]+" + _S + "*[wW]" + _S + "*" + _NS + "+",
    r"^\$" + _S + "*[wW]" + _S + "*" + _NS + "+",
    "^/[^/]*/[IMim]*" + _S + "*[wW]" + _S + "*" + _NS + "+",
    "^[0-9]+,[0-9]+" + _S + "*[wW]" + _S + "*" + _NS + "+",
    r"^[0-9]+,\$" + _S + "*[wW]" + _S + "*" + _NS + "+",
    "^/[^/]*/[IMim]*,/[^/]*/[IMim]*" + _S + "*[wW]" + _S + "*" + _NS + "+",
))
_EXECUTE_COMMAND_PATTERNS = tuple(re.compile(p) for p in (
    "^e",
    "^[0-9]+" + _S + "*e",
    r"^\$" + _S + "*e",
    "^/[^/]*/[IMim]*" + _S + "*e",
    "^[0-9]+,[0-9]+" + _S + "*e",
    r"^[0-9]+,\$" + _S + "*e",
    "^/[^/]*/[IMim]*,/[^/]*/[IMim]*" + _S + "*e",
))
_PROPER_SUBST = re.compile(r"^s([^\\\n])" + _DOT + r"*?\1" + _DOT + r"*?\1[^wWeE]*\Z")
_SUBST = re.compile(r"s([^\\\n])" + _DOT + r"*?\1" + _DOT + r"*?\1(" + _DOT + r"*?)\Z")


def contains_dangerous_operations(expression: str) -> bool:
    """`containsDangerousOperations`: the denylist, applied after the allowlist matched."""
    cmd = expression.strip(_JS_TRIM)
    if not cmd:
        return False
    if re.search("[^\x01-\x7f]", cmd):
        return True
    if "{" in cmd or "}" in cmd or "\n" in cmd:
        return True
    hash_index = cmd.find("#")
    if hash_index != -1 and not (hash_index > 0 and cmd[hash_index - 1] == "s"):
        return True
    if cmd.startswith("!") or re.search(r"[/0-9$]!", cmd):
        return True
    if re.search("[0-9]" + _S + "*~" + _S + "*[0-9]|," + _S + "*~" + _S + r"*[0-9]|\$" + _S + "*~" + _S + "*[0-9]",
                 cmd):
        return True
    if cmd.startswith(","):
        return True
    if re.search("," + _S + "*[+-]", cmd):
        return True
    if re.search(r"s\\", cmd) or re.search(r"\\[|#%@]", cmd):
        return True
    if re.search(r"\\/" + _DOT + "*[wW]", cmd):
        return True
    if re.search("/[^/]*" + _S + "+[wWeE]", cmd):
        return True
    if cmd.startswith("s/") and not re.fullmatch("s/[^/]*/[^/]*/[^/]*", cmd):
        return True
    if re.match("^s" + _DOT, cmd) and re.search(r"[wWeE]\Z", cmd) and not _PROPER_SUBST.search(cmd):
        return True
    if any(p.search(cmd) for p in _WRITE_COMMAND_PATTERNS):
        return True
    if any(p.search(cmd) for p in _EXECUTE_COMMAND_PATTERNS):
        return True
    m = _SUBST.search(cmd)
    if m and any(ch in (m.group(2) or "") for ch in "wWeE"):
        return True
    return re.search(r"y([^\\\n])", cmd) is not None and re.search("[wWeE]", cmd) is not None


def sed_command_is_allowed_by_allowlist(command: str, *, allow_file_writes: bool = False) -> bool:
    """`sedCommandIsAllowedByAllowlist`."""
    try:
        expressions = extract_sed_expressions(command)
    except SedParseError:
        return False
    has_files = has_file_args(command)
    if allow_file_writes:
        is_print = False
        is_subst = is_substitution_command(command, expressions, has_files, allow_file_writes=True)
    else:
        is_print = is_line_printing_command(command, expressions)
        is_subst = is_substitution_command(command, expressions, has_files)
    if not is_print and not is_subst:
        return False
    if is_subst and any(";" in e for e in expressions):
        return False
    return not any(contains_dangerous_operations(e) for e in expressions)


def check_sed_constraints(command: str, context: ToolPermissionContext) -> PermissionResult:
    """`checkSedConstraints`: 'ask' if any sed subcommand is not on the allowlist."""
    for cmd in split_command(command):
        trimmed = cmd.strip(_JS_TRIM)
        base = re.split(_S + "+", trimmed)[0]
        if base != "sed":
            continue
        allow_writes = context.mode == PermissionMode.ACCEPT_EDITS
        if not sed_command_is_allowed_by_allowlist(trimmed, allow_file_writes=allow_writes):
            return PermissionResult(
                behavior=PermissionBehavior.ASK,
                message=SED_ASK_MESSAGE,
                decision_reason={"type": "other", "reason": SED_ASK_REASON},
            )
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message="No dangerous sed operations detected")


__all__ = [
    "check_sed_constraints",
    "contains_dangerous_operations",
    "extract_sed_expressions",
    "has_file_args",
    "is_line_printing_command",
    "is_print_command",
    "is_substitution_command",
    "sed_command_is_allowed_by_allowlist",
]
