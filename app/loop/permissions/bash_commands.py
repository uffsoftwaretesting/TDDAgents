"""
Splitting a shell command into subcommands and stripping its output redirections.

Ported from (v2.1.88):

- `src/utils/bash/commands.ts` -> `splitCommandWithOperators`, `splitCommand_DEPRECATED`,
  `filterControlOperators`, `isCommandList`, `isUnsafeCompoundCommand_DEPRECATED`,
  `extractOutputRedirections` and its helpers (`handleRedirection`,
  `handleFileDescriptorRedirection`, `isSimpleTarget`, `hasDangerousExpansion`,
  `isStaticRedirectTarget`, `reconstructCommand`, `needsQuoting`,
  `detectCommandSubstitution`).
- `src/utils/bash/ParsedCommand.ts` -> `RegexParsedCommand_DEPRECATED.getPipeSegments` /
  `withoutOutputRedirections`, the implementation upstream uses when tree-sitter is
  unavailable — which is always, here.
- the `shell-quote` npm package (v1.10.0, `quote.js`) -> `quote`.

Parsing reuses the `shell-quote` port in `bash_security.py`, called with the env function
upstream passes (`varName => '$' + varName`) so variables stay visible in the split output.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Literal

from app.loop.permissions.bash_security import (
    ParseEntry,
    extract_heredocs_with_map,
    restore_heredocs,
    try_parse_shell_command,
)

ALLOWED_FILE_DESCRIPTORS: frozenset[str] = frozenset({"0", "1", "2"})

COMMAND_LIST_SEPARATORS: frozenset[str] = frozenset({"&&", "||", ";", ";;", "|"})
ALL_SUPPORTED_CONTROL_OPERATORS: frozenset[str] = COMMAND_LIST_SEPARATORS | {">&", ">", ">>"}

RedirectOperator = Literal[">", ">>"]


def _preserve_variable(name: str) -> str:
    return "$" + name


def _join_continuations(text: str) -> str:
    """Odd backslash runs before a newline are a continuation: drop one backslash and the newline."""

    def repl(m: re.Match[str]) -> str:
        count = len(m.group(0)) - 1
        return "\\" * (count - 1) if count % 2 == 1 else m.group(0)

    return re.sub(r"\\+\n", repl, text)


def is_static_redirect_target(target: str) -> bool:
    """`isStaticRedirectTarget`: a single literal shell word with no expansion."""
    if re.search(r"[\s'\"]", target):
        return False
    if not target:
        return False
    if target.startswith("#"):
        return False
    return not (
        target.startswith("!")
        or target.startswith("=")
        or "$" in target
        or "`" in target
        or "*" in target
        or "?" in target
        or "[" in target
        or "{" in target
        or "~" in target
        or "(" in target
        or "<" in target
        or target.startswith("&")
    )


def _placeholders() -> dict[str, str]:
    salt = secrets.token_hex(8)
    return {
        "SINGLE_QUOTE": f"__SINGLE_QUOTE_{salt}__",
        "DOUBLE_QUOTE": f"__DOUBLE_QUOTE_{salt}__",
        "NEW_LINE": f"__NEW_LINE_{salt}__",
        "ESCAPED_OPEN_PAREN": f"__ESCAPED_OPEN_PAREN_{salt}__",
        "ESCAPED_CLOSE_PAREN": f"__ESCAPED_CLOSE_PAREN_{salt}__",
    }


def split_command_with_operators(command: str) -> list[str]:
    """`splitCommandWithOperators`: subcommands and the operators between them."""
    ph = _placeholders()
    processed, heredocs = extract_heredocs_with_map(command)
    joined = _join_continuations(processed)
    original_joined = _join_continuations(command)

    prepared = (
        joined.replace('"', '"' + ph["DOUBLE_QUOTE"])
        .replace("'", "'" + ph["SINGLE_QUOTE"])
        .replace("\n", "\n" + ph["NEW_LINE"] + "\n")
        .replace("\\(", ph["ESCAPED_OPEN_PAREN"])
        .replace("\\)", ph["ESCAPED_CLOSE_PAREN"])
    )
    parsed = try_parse_shell_command(prepared, _preserve_variable)
    if not parsed.success:
        return [original_joined]
    tokens = parsed.tokens
    if not tokens:
        return []

    parts: list[ParseEntry | None] = []
    for part in tokens:
        if isinstance(part, str):
            if parts and isinstance(parts[-1], str):
                if part == ph["NEW_LINE"]:
                    parts.append(None)
                else:
                    parts[-1] = parts[-1] + " " + part
                continue
        elif part.get("op") == "glob":
            if parts and isinstance(parts[-1], str):
                parts[-1] = parts[-1] + " " + part["pattern"]
                continue
        parts.append(part)

    string_parts: list[str] = []
    for item in parts:
        if item is None:
            continue
        if isinstance(item, str):
            string_parts.append(item)
        elif "comment" in item:
            cleaned = item["comment"].replace('"' + ph["DOUBLE_QUOTE"], ph["DOUBLE_QUOTE"]).replace(
                "'" + ph["SINGLE_QUOTE"], ph["SINGLE_QUOTE"]
            )
            string_parts.append("#" + cleaned)
        elif item.get("op") == "glob":
            string_parts.append(item["pattern"])
        else:
            string_parts.append(item["op"])

    quoted = [
        p.replace(ph["SINGLE_QUOTE"], "'")
        .replace(ph["DOUBLE_QUOTE"], '"')
        .replace("\n" + ph["NEW_LINE"] + "\n", "\n")
        .replace(ph["ESCAPED_OPEN_PAREN"], "\\(")
        .replace(ph["ESCAPED_CLOSE_PAREN"], "\\)")
        for p in string_parts
    ]
    return restore_heredocs(quoted, heredocs)


def filter_control_operators(commands_and_operators: list[str]) -> list[str]:
    """`filterControlOperators`."""
    return [p for p in commands_and_operators if p not in ALL_SUPPORTED_CONTROL_OPERATORS]


def split_command(command: str) -> list[str]:
    """
    `splitCommand_DEPRECATED`: the subcommands, with static output redirections and
    standard-fd duplications (`2>&1`, `> /dev/null`, `> out.txt`) stripped. Dynamic targets
    (`> $FILE`, `> ~/x`) are kept, so they stay visible to permission checks.
    """
    parts: list[str | None] = list(split_command_with_operators(command))
    for i, part in enumerate(parts):
        if part is None:
            continue
        if part not in (">&", ">", ">>"):
            continue
        prev_raw = parts[i - 1] if i > 0 else None
        prev_part = prev_raw.strip(_JS_TRIM_CHARS) if prev_raw is not None else None
        next_raw = parts[i + 1] if i + 1 < len(parts) else None
        after_raw = parts[i + 2] if i + 2 < len(parts) else None
        next_part = next_raw.strip(_JS_TRIM_CHARS) if next_raw is not None else None
        after_next = after_raw.strip(_JS_TRIM_CHARS) if after_raw is not None else None
        if next_part is None:
            continue

        should_strip = False
        strip_third = False
        effective_next = next_part
        if (
            part in (">", ">>")
            and len(next_part) >= 3
            and next_part[-2] == " "
            and next_part[-1] in ALLOWED_FILE_DESCRIPTORS
            and after_next in (">", ">>", ">&")
        ):
            effective_next = next_part[:-2]

        if part == ">&" and next_part in ALLOWED_FILE_DESCRIPTORS:
            should_strip = True
        elif part == ">" and next_part == "&" and after_next is not None and after_next in ALLOWED_FILE_DESCRIPTORS:
            should_strip = True
            strip_third = True
        elif (
            part == ">"
            and next_part.startswith("&")
            and len(next_part) > 1
            and next_part[1:] in ALLOWED_FILE_DESCRIPTORS
        ):
            should_strip = True
        elif part in (">", ">>") and is_static_redirect_target(effective_next):
            should_strip = True

        if should_strip:
            if (
                prev_part
                and len(prev_part) >= 3
                and prev_part[-1] in ALLOWED_FILE_DESCRIPTORS
                and prev_part[-2] == " "
            ):
                parts[i - 1] = prev_part[:-2]
            parts[i] = None
            parts[i + 1] = None
            if strip_third:
                parts[i + 2] = None

    return filter_control_operators([p for p in parts if p is not None and p != ""])


def is_command_list(command: str) -> bool:
    """`isCommandList`: only separators, globs, and stdout/stderr redirections."""
    ph = _placeholders()
    processed = extract_heredocs_with_map(command)[0]
    parsed = try_parse_shell_command(
        processed.replace('"', '"' + ph["DOUBLE_QUOTE"]).replace("'", "'" + ph["SINGLE_QUOTE"]),
        _preserve_variable,
    )
    if not parsed.success:
        return False
    tokens = parsed.tokens
    for i, part in enumerate(tokens):
        if isinstance(part, str):
            continue
        if "comment" in part:
            return False
        op = part["op"]
        if op == "glob" or op in COMMAND_LIST_SEPARATORS:
            continue
        if op == ">&":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if isinstance(nxt, str) and nxt.strip(_JS_TRIM_CHARS) in ALLOWED_FILE_DESCRIPTORS:
                continue
        elif op in (">", ">>"):
            continue
        return False
    return True


def is_unsafe_compound_command(command: str) -> bool:
    """`isUnsafeCompoundCommand_DEPRECATED`: subshells, groups, or anything unparseable."""
    processed = extract_heredocs_with_map(command)[0]
    if not try_parse_shell_command(processed, _preserve_variable).success:
        return True
    return len(split_command(command)) > 1 and not is_command_list(command)


# ── extractOutputRedirections ────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OutputRedirection:
    target: str
    operator: RedirectOperator


@dataclass(frozen=True, slots=True)
class RedirectionExtraction:
    command_without_redirections: str
    redirections: tuple[OutputRedirection, ...] = field(default_factory=tuple)
    has_dangerous_redirection: bool = False


def _is_operator(part: ParseEntry | None, op: str) -> bool:
    return isinstance(part, dict) and part.get("op") == op


def is_simple_target(target: ParseEntry | None) -> bool:
    """`isSimpleTarget`: a non-empty literal path with no expansion."""
    if not isinstance(target, str) or not target:
        return False
    return not (
        target.startswith("!")
        or target.startswith("=")
        or target.startswith("~")
        or "$" in target
        or "`" in target
        or "*" in target
        or "?" in target
        or "[" in target
        or "{" in target
    )


def has_dangerous_expansion(target: ParseEntry | None) -> bool:
    """`hasDangerousExpansion`: covers every non-empty string `is_simple_target` rejects."""
    if isinstance(target, dict):
        return target.get("op") == "glob"
    if not isinstance(target, str) or not target:
        return False
    return (
        "$" in target
        or "%" in target
        or "`" in target
        or "*" in target
        or "?" in target
        or "[" in target
        or "{" in target
        or target.startswith("!")
        or target.startswith("=")
        or target.startswith("~")
    )


def _is_fd(part: ParseEntry | None) -> bool:
    return isinstance(part, str) and re.fullmatch(r"[0-9]+", part.strip(_JS_TRIM_CHARS)) is not None


def _bang_target(nxt: ParseEntry | None) -> str | None:
    """`>!file` (no space): the zsh force-clobber target, excluding history expansions."""
    if (
        isinstance(nxt, str)
        and nxt.startswith("!")
        and len(nxt) > 1
        and nxt[1] not in ("!", "-", "?")
        and not re.match(r"![0-9]", nxt)
    ):
        return nxt[1:]
    return None


def _handle_fd_redirection(
    fd: str,
    operator: RedirectOperator,
    target: ParseEntry | None,
    redirections: list[OutputRedirection],
    kept: list[ParseEntry],
    skip_count: int = 1,
) -> tuple[int, bool]:
    """`handleFileDescriptorRedirection` -> (skip, dangerous)."""
    is_stdout = fd == "1"
    is_file_target = (
        is_simple_target(target) and isinstance(target, str) and re.fullmatch(r"[0-9]+", target) is None
    )
    is_fd_target = isinstance(target, str) and re.fullmatch(r"[0-9]+", target.strip(_JS_TRIM_CHARS)) is not None

    if kept:
        kept.pop()
    if not is_fd_target and has_dangerous_expansion(target):
        return 0, True
    if is_file_target:
        assert isinstance(target, str)
        redirections.append(OutputRedirection(target, operator))
        if not is_stdout:
            kept.extend([fd + operator, target])
        return skip_count, False
    if not is_stdout:
        kept.append(fd + operator)
        if target:
            kept.append(target)
            return 1, False
    return 0, False


def _handle_redirection(
    part: ParseEntry,
    prev: ParseEntry | None,
    nxt: ParseEntry | None,
    next_next: ParseEntry | None,
    next_next_next: ParseEntry | None,
    redirections: list[OutputRedirection],
    kept: list[ParseEntry],
) -> tuple[int, bool]:
    """`handleRedirection` -> (skip, dangerous)."""
    if _is_operator(part, ">") or _is_operator(part, ">>"):
        assert isinstance(part, dict)
        operator: RedirectOperator = ">>" if part["op"] == ">>" else ">"

        if _is_fd(prev):
            assert isinstance(prev, str)
            fd = prev.strip(_JS_TRIM_CHARS)
            if nxt == "!" and is_simple_target(next_next):
                return _handle_fd_redirection(fd, operator, next_next, redirections, kept, 2)
            if nxt == "!" and has_dangerous_expansion(next_next):
                return 0, True
            if _is_operator(nxt, "|") and is_simple_target(next_next):
                return _handle_fd_redirection(fd, operator, next_next, redirections, kept, 2)
            if _is_operator(nxt, "|") and has_dangerous_expansion(next_next):
                return 0, True
            after_bang = _bang_target(nxt)
            if after_bang is not None:
                if has_dangerous_expansion(after_bang):
                    return 0, True
                return _handle_fd_redirection(fd, operator, after_bang, redirections, kept, 1)
            return _handle_fd_redirection(fd, operator, nxt, redirections, kept, 1)

        if _is_operator(nxt, "|") and is_simple_target(next_next):
            assert isinstance(next_next, str)
            redirections.append(OutputRedirection(next_next, operator))
            return 2, False
        if _is_operator(nxt, "|") and has_dangerous_expansion(next_next):
            return 0, True
        if nxt == "!" and is_simple_target(next_next):
            assert isinstance(next_next, str)
            redirections.append(OutputRedirection(next_next, operator))
            return 2, False
        if nxt == "!" and has_dangerous_expansion(next_next):
            return 0, True
        after_bang = _bang_target(nxt)
        if after_bang is not None:
            if has_dangerous_expansion(after_bang):
                return 0, True
            redirections.append(OutputRedirection(after_bang, operator))
            return 1, False
        if _is_operator(nxt, "&"):
            if next_next == "!" and is_simple_target(next_next_next):
                assert isinstance(next_next_next, str)
                redirections.append(OutputRedirection(next_next_next, operator))
                return 3, False
            if next_next == "!" and has_dangerous_expansion(next_next_next):
                return 0, True
            if _is_operator(next_next, "|") and is_simple_target(next_next_next):
                assert isinstance(next_next_next, str)
                redirections.append(OutputRedirection(next_next_next, operator))
                return 3, False
            if _is_operator(next_next, "|") and has_dangerous_expansion(next_next_next):
                return 0, True
            if is_simple_target(next_next):
                assert isinstance(next_next, str)
                redirections.append(OutputRedirection(next_next, operator))
                return 2, False
            if has_dangerous_expansion(next_next):
                return 0, True
        if is_simple_target(nxt):
            assert isinstance(nxt, str)
            redirections.append(OutputRedirection(nxt, operator))
            return 1, False
        if has_dangerous_expansion(nxt):
            return 0, True

    if _is_operator(part, ">&"):
        if _is_fd(prev) and _is_fd(nxt):
            return 0, False
        if _is_operator(nxt, "|") and is_simple_target(next_next):
            assert isinstance(next_next, str)
            redirections.append(OutputRedirection(next_next, ">"))
            return 2, False
        if _is_operator(nxt, "|") and has_dangerous_expansion(next_next):
            return 0, True
        if nxt == "!" and is_simple_target(next_next):
            assert isinstance(next_next, str)
            redirections.append(OutputRedirection(next_next, ">"))
            return 2, False
        if nxt == "!" and has_dangerous_expansion(next_next):
            return 0, True
        if is_simple_target(nxt) and not _is_fd(nxt):
            assert isinstance(nxt, str)
            redirections.append(OutputRedirection(nxt, ">"))
            return 1, False
        if not _is_fd(nxt) and has_dangerous_expansion(nxt):
            return 0, True

    return 0, False


def extract_output_redirections(cmd: str) -> RedirectionExtraction:
    """
    `extractOutputRedirections`: the command without its output redirections, the
    redirection targets, and whether any target uses expansion that cannot be validated.
    Fails closed: an unparseable command reports a dangerous redirection.
    """
    heredoc_extracted, heredocs = extract_heredocs_with_map(cmd)
    processed = _join_continuations(heredoc_extracted)
    parsed_result = try_parse_shell_command(processed, _preserve_variable)
    if not parsed_result.success:
        return RedirectionExtraction(cmd, (), True)
    parsed = parsed_result.tokens

    redirected_subshells: set[int] = set()
    paren_stack: list[tuple[int, bool]] = []
    for i, part in enumerate(parsed):
        if _is_operator(part, "("):
            prev = parsed[i - 1] if i > 0 else None
            is_start = i == 0 or (isinstance(prev, dict) and prev.get("op") in ("&&", "||", ";", "|"))
            paren_stack.append((i, is_start))
        elif _is_operator(part, ")") and paren_stack:
            opening, opening_is_start = paren_stack.pop()
            nxt = parsed[i + 1] if i + 1 < len(parsed) else None
            if opening_is_start and (_is_operator(nxt, ">") or _is_operator(nxt, ">>")):
                redirected_subshells.add(opening)
                redirected_subshells.add(i)

    redirections: list[OutputRedirection] = []
    kept: list[ParseEntry] = []
    dangerous = False
    cmd_sub_depth = 0
    i = 0
    while i < len(parsed):
        part = parsed[i]
        if not part:
            i += 1
            continue
        prev = parsed[i - 1] if i > 0 else None
        nxt = parsed[i + 1] if i + 1 < len(parsed) else None

        if (_is_operator(part, "(") or _is_operator(part, ")")) and i in redirected_subshells:
            i += 1
            continue

        if _is_operator(part, "(") and isinstance(prev, str) and prev.endswith("$"):
            cmd_sub_depth += 1
        elif _is_operator(part, ")") and cmd_sub_depth > 0:
            cmd_sub_depth -= 1

        if cmd_sub_depth == 0:
            skip, is_dangerous = _handle_redirection(
                part,
                prev,
                nxt,
                parsed[i + 2] if i + 2 < len(parsed) else None,
                parsed[i + 3] if i + 3 < len(parsed) else None,
                redirections,
                kept,
            )
            if is_dangerous:
                dangerous = True
            if skip > 0:
                i += skip + 1
                continue

        kept.append(part)
        i += 1

    without = restore_heredocs([reconstruct_command(kept, processed)], heredocs)[0]
    return RedirectionExtraction(without, tuple(redirections), dangerous)


# ── reconstructCommand and shell-quote `quote` ───────────────────────────────

_QUOTE_OPS: tuple[str, ...] = (
    "||", "&&", ";;", "|&", "<(", "<<<", ">>", ">&", "<&", "&", ";", "(", ")", "|", "<", ">",
)
_LINE_TERMINATORS = re.compile("[\n\r\u2028\u2029]")
_GLOB_SHELL_SPECIAL = re.compile(r"""[\s#!"$&'():;<=>@\\^`|]""")
_JS_WS = "\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
#: `String.prototype.trim`'s whitespace set, spelled out (no range: `str.strip` takes chars).
_JS_TRIM_CHARS = (
    "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def shell_quote(xs: list[ParseEntry]) -> str:
    """`shell-quote`'s `quote(xs)`."""
    out: list[str] = []
    for s in xs:
        if s == "":
            out.append("''")
        elif isinstance(s, dict):
            if s.get("op") == "glob":
                pattern = s.get("pattern")
                if not isinstance(pattern, str):
                    raise TypeError("glob token requires a string `pattern`")
                if _LINE_TERMINATORS.search(pattern):
                    raise TypeError("glob `pattern` must not contain line terminators")
                out.append(_GLOB_SHELL_SPECIAL.sub(lambda m: "\\" + m.group(0), pattern))
            elif isinstance(s.get("op"), str):
                if s["op"] not in _QUOTE_OPS:
                    raise TypeError("invalid `op` value: " + repr(s["op"]))
                out.append("".join("\\" + ch for ch in s["op"]))
            elif isinstance(s.get("comment"), str):
                if _LINE_TERMINATORS.search(s["comment"]):
                    raise TypeError("`comment` must not contain line terminators")
                out.append("#" + s["comment"])
            else:
                raise TypeError("unrecognized object token shape")
        elif re.search('["' + _JS_WS + r"\\]", s) and "'" not in s:
            out.append("'" + s + "'")
        elif re.search("[\"'" + _JS_WS + "]", s):
            out.append('"' + re.sub(r'(["\\$`!])', r"\\\1", s) + '"')
        else:
            out.append(re.sub(r"([A-Za-z]:)?([#!\"$&'()*,:;<=>?@[\\\]^`{|}~])", r"\1\\\2", s))
    return " ".join(out)


def _needs_quoting(s: str) -> bool:
    if re.fullmatch(r"[0-9]+>>?", s):
        return False
    if re.search("[" + _JS_WS + "]", s):
        return True
    return len(s) == 1 and s in "><|&;()"


def _add_token(result: str, token: str, no_space: bool = False) -> str:
    if not result or no_space:
        return result + token
    return result + " " + token


def _detect_command_substitution(prev: ParseEntry | None, kept: list[ParseEntry], index: int) -> bool:
    if not isinstance(prev, str) or not prev:
        return False
    if prev == "$":
        return True
    if prev.endswith("$"):
        if "=" in prev and prev.endswith("=$"):
            return True
        depth = 1
        j = index + 1
        while j < len(kept) and depth > 0:
            if _is_operator(kept[j], "("):
                depth += 1
            if _is_operator(kept[j], ")"):
                depth -= 1
                if depth == 0:
                    after = kept[j + 1] if j + 1 < len(kept) else None
                    return isinstance(after, str) and bool(after) and not after.startswith(" ")
            j += 1
    return False


def reconstruct_command(kept: list[ParseEntry], original_cmd: str) -> str:
    """`reconstructCommand`: rebuild a command string from the kept tokens."""
    if not kept:
        return original_cmd
    result = ""
    cmd_sub_depth = 0
    in_process_sub = False
    i = 0
    while i < len(kept):
        part = kept[i]
        prev = kept[i - 1] if i > 0 else None
        nxt = kept[i + 1] if i + 1 < len(kept) else None

        if isinstance(part, str):
            if re.search(r"[|&;]", part):
                text = f'"{part}"'
            elif _needs_quoting(part):
                text = shell_quote([part])
            else:
                text = part
            no_space = result.endswith("(") or prev == "$" or _is_operator(prev, ")")
            if result.endswith("<("):
                result += " " + text
            else:
                result = _add_token(result, text, no_space)
            i += 1
            continue

        op = part.get("op")
        if not isinstance(op, str):
            i += 1
            continue
        if op == "glob":
            result = _add_token(result, part["pattern"])
            i += 1
            continue
        if (
            op == ">&"
            and isinstance(prev, str)
            and re.fullmatch(r"[0-9]+", prev)
            and isinstance(nxt, str)
            and re.fullmatch(r"[0-9]+", nxt)
        ):
            last = result.rfind(prev)
            result = result[:last] + prev + op + nxt
            i += 2
            continue
        if op == "<" and _is_operator(nxt, "<"):
            delimiter = kept[i + 2] if i + 2 < len(kept) else None
            if isinstance(delimiter, str) and delimiter:
                result = _add_token(result, delimiter)
                i += 3
                continue
        if op == "<<<":
            result = _add_token(result, op)
            i += 1
            continue
        if op == "(":
            if _detect_command_substitution(prev, kept, i) or cmd_sub_depth > 0:
                cmd_sub_depth += 1
                if result.endswith(" "):
                    result = result[:-1]
                result += "("
            elif result.endswith("$"):
                result = _add_token(result, "(")
            else:
                result = _add_token(result, "(", result.endswith("<(") or result.endswith("("))
            i += 1
            continue
        if op == ")":
            if in_process_sub:
                in_process_sub = False
                result += ")"
                i += 1
                continue
            if cmd_sub_depth > 0:
                cmd_sub_depth -= 1
            result += ")"
            i += 1
            continue
        if op == "<(":
            in_process_sub = True
            result = _add_token(result, op)
            i += 1
            continue
        if op in ("&&", "||", "|", ";", ">", ">>", "<"):
            result = _add_token(result, op)
        i += 1

    return result.strip(_JS_TRIM_CHARS) or original_cmd


# ── RegexParsedCommand_DEPRECATED ────────────────────────────────────────────

def get_pipe_segments(command: str) -> list[str]:
    """`getPipeSegments`: the command split on `|`, each segment re-joined with spaces."""
    try:
        parts = split_command_with_operators(command)
    except Exception:
        return [command]
    segments: list[str] = []
    current: list[str] = []
    for part in parts:
        if part == "|":
            if current:
                segments.append(" ".join(current))
                current = []
        else:
            current.append(part)
    if current:
        segments.append(" ".join(current))
    return segments or [command]


def without_output_redirections(command: str) -> str:
    """`withoutOutputRedirections`: strip redirections only when some were found."""
    if ">" not in command:
        return command
    extraction = extract_output_redirections(command)
    return extraction.command_without_redirections if extraction.redirections else command


__all__ = [
    "ALLOWED_FILE_DESCRIPTORS",
    "ALL_SUPPORTED_CONTROL_OPERATORS",
    "COMMAND_LIST_SEPARATORS",
    "OutputRedirection",
    "RedirectionExtraction",
    "extract_output_redirections",
    "filter_control_operators",
    "get_pipe_segments",
    "has_dangerous_expansion",
    "is_command_list",
    "is_simple_target",
    "is_static_redirect_target",
    "is_unsafe_compound_command",
    "reconstruct_command",
    "shell_quote",
    "split_command",
    "split_command_with_operators",
    "without_output_redirections",
]
