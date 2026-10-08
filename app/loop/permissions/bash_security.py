"""
Bash command-injection detection, ported from claude-code's legacy validator battery.

Ported from (v2.1.88):

- `src/tools/BashTool/bashSecurity.ts` -> `bashCommandIsSafe_DEPRECATED` and every validator
  it runs, in the same order, with the same messages and the same
  `isBashSecurityCheckForMisparsing` semantics; plus `stripSafeHeredocSubstitutions` /
  `hasSafeHeredocSubstitution`.
- `src/utils/bash/heredoc.ts` -> `extractHeredocs` (only `processedCommand` is needed here).
- `src/utils/bash/shellQuote.ts` -> `tryParseShellCommand`, `hasMalformedTokens`,
  `hasShellQuoteSingleQuoteBug`.
- the `shell-quote` npm package (v1.10.0, `parse.js`) -> `parse(cmd)` with no env and no
  options, which is exactly how `tryParseShellCommand(cmd)` calls it.
- `src/tools/BashTool/bashPermissions.ts` -> `bashToolHasPermission`, the three places it
  consumes the battery on the legacy (no tree-sitter) path, composed in
  `bash_tool_security_check`.

This is the *legacy* path, which upstream runs whenever tree-sitter-bash is unavailable.
There is no tree-sitter here, so it is the path that applies. Telemetry (`logEvent`) is
dropped. There is no LLM classifier and no substring blocklist anywhere in upstream's
decision, so there is none here.

Where Python necessarily differs from the JavaScript, the JS semantics are reproduced
explicitly rather than inherited from `re`:

- JS `\\s` is a specific set (`[\\t\\n\\v\\f\\r ]` plus Unicode spaces including U+FEFF);
  Python's differs (it adds U+001C-001F and U+0085, drops U+FEFF). `_WS` / `_NWS` spell the
  JS set out. `str.trim()` is `_js_trim` for the same reason.
- JS `.` also excludes `\\r`, U+2028 and U+2029; `_DOT` spells that out.
- JS `$` without the `m` flag means end of input; Python's `$` also matches before a final
  newline, so every anchor is `\\Z`.
- JS `\\w` / `\\b` are ASCII-only: `re.ASCII` or explicit classes.
- JS indexes strings by UTF-16 code unit, Python by code point. Every character any
  validator inspects is in the BMP, so the only effect is on astral-plane characters, which
  no validator matches either way.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Callable, Union

from app.loop.permissions.types import PermissionBehavior, PermissionResult

# ── JavaScript regex semantics ────────────────────────────────────────────────

_WS_CHARS = "\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WS = "[" + _WS_CHARS + "]"
_NWS = "[^" + _WS_CHARS + "]"
_DOT = "[^\n\r\u2028\u2029]"
_JS_TRIM_CHARS = "\t\n\x0b\x0c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" \
    "\u2028\u2029\u202f\u205f\u3000\ufeff"


def _js_trim(s: str) -> str:
    return s.strip(_JS_TRIM_CHARS)


def _is_js_ws(c: str) -> bool:
    return c != "" and c in _JS_TRIM_CHARS


# ── Result helpers ────────────────────────────────────────────────────────────

def _passthrough(message: str) -> PermissionResult:
    return PermissionResult(behavior=PermissionBehavior.PASSTHROUGH, message=message)


def _ask(message: str) -> PermissionResult:
    return PermissionResult(behavior=PermissionBehavior.ASK, message=message)


def _allow(command: str, reason: str) -> PermissionResult:
    return PermissionResult(
        behavior=PermissionBehavior.ALLOW,
        updated_input={"command": command},
        decision_reason={"type": "other", "reason": reason},
    )


# ── Constants (bashSecurity.ts) ───────────────────────────────────────────────

HEREDOC_IN_SUBSTITUTION = re.compile(r"\$\(" + _DOT + "*<<")

#: `COMMAND_SUBSTITUTION_PATTERNS`. Backticks are handled separately in
#: `validate_dangerous_patterns` to distinguish escaped from unescaped.
COMMAND_SUBSTITUTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<\("), "process substitution <()"),
    (re.compile(r">\("), "process substitution >()"),
    (re.compile(r"=\("), "Zsh process substitution =()"),
    # Zsh EQUALS expansion: =cmd at word start expands to $(which cmd).
    (re.compile(r"(?:^|[" + _WS_CHARS + r";&|])=[a-zA-Z_]"), "Zsh equals expansion (=cmd)"),
    (re.compile(r"\$\("), "$() command substitution"),
    (re.compile(r"\$\{"), "${} parameter substitution"),
    (re.compile(r"\$\["), "$[] legacy arithmetic expansion"),
    (re.compile(r"~\["), "Zsh-style parameter expansion"),
    (re.compile(r"\(e:"), "Zsh-style glob qualifiers"),
    (re.compile(r"\(\+"), "Zsh glob qualifier with command execution"),
    (re.compile(r"\}" + _WS + r"*always" + _WS + r"*\{"), "Zsh always block (try/always construct)"),
    # Defense in depth: PowerShell comment syntax.
    (re.compile(r"<#"), "PowerShell comment syntax"),
)

#: `ZSH_DANGEROUS_COMMANDS`, checked against the base command.
ZSH_DANGEROUS_COMMANDS: frozenset[str] = frozenset({
    "zmodload", "emulate",
    "sysopen", "sysread", "syswrite", "sysseek",
    "zpty", "ztcp", "zsocket", "mapfile",
    "zf_rm", "zf_mv", "zf_ln", "zf_chmod", "zf_chown", "zf_mkdir", "zf_rmdir", "zf_chgrp",
})

ZSH_PRECOMMAND_MODIFIERS: frozenset[str] = frozenset({"command", "builtin", "noglob", "nocorrect"})

#: Non-printable control characters: 0x00-0x08, 0x0B-0x0C, 0x0E-0x1F, 0x7F.
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

UNICODE_WS_RE = re.compile("[\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]")

SHELL_OPERATORS: frozenset[str] = frozenset({";", "|", "&", "<", ">"})

_SAFE_HEREDOC_PATTERN = re.compile(
    r"\$\(cat[ \t]*<<(-?)[ \t]*(?:'+([A-Za-z_][A-Za-z0-9_]*)'+|\\([A-Za-z_][A-Za-z0-9_]*))"
)


# ── shell-quote `parse` (v1.10.0) ─────────────────────────────────────────────

ParseEntry = Union[str, dict[str, str]]

_CONTROL = r"(?:\|\||\&\&|;;|\|\&|\<\(|\<\<\<|>>|>\&|<\&|[&;()|<>])"
_CONTROL_RE = re.compile("^" + _CONTROL + r"\Z")
_META = "|&;()<> \t"
_META_CLASS = re.escape(_META)
_BAREWORD = r"(\\['\"" + _META_CLASS + r"]|[^" + _WS_CHARS + r"'\"" + _META_CLASS + r"])+"
_SINGLE_QUOTE = r"'([^']*?)'"
_DOUBLE_QUOTE = r'"((\\"|[^"])*?)"'
_CHUNKER = re.compile("(" + _CONTROL + ")|(" + _BAREWORD + "|" + _DOUBLE_QUOTE + "|" + _SINGLE_QUOTE + ")+")
_ENV_SPECIAL = re.compile(r"[*@#?$!_-]")
_ENV_NAME_END = re.compile(r"[^A-Za-z0-9_]")


class ShellParseError(ValueError):
    """`shell-quote` throws `Error('Bad substitution: ...')`; this is that error."""


ShellEnv = Callable[[str], str]


def shell_quote_parse(string: str, env: ShellEnv | None = None) -> list[ParseEntry]:
    """
    Port of `shell-quote`'s `parse(string, env)` with no options.

    Without `env`, undefined variables expand to '' (and a bare `$` with no name to '$'),
    exactly as `getVar` does with an empty env object. With a function `env`, a variable
    expands to `env(name)` — the form `splitCommandWithOperators` uses
    (`varName => '$' + varName`) to keep variables visible. Throws `ShellParseError` where
    upstream throws.
    """
    out_entries: list[ParseEntry] = []
    commented = False

    for match in _CHUNKER.finditer(string):
        s = match.group(0)
        if not s or commented:
            continue
        if _CONTROL_RE.match(s):
            out_entries.append({"op": s})
            continue

        quote = ""
        esc = False
        out = ""
        is_glob = False
        i = 0
        result: list[ParseEntry] | None = None

        def parse_env_var() -> str:
            nonlocal i
            i += 1
            char = s[i] if i < len(s) else ""
            if char == "{":
                i += 1
                if (s[i] if i < len(s) else "") == "}":
                    raise ShellParseError("Bad substitution: " + s[i - 2:i + 1])
                depth = 1
                varend = i
                while depth > 0 and varend < len(s):
                    if s[varend] == "{" and varend > 0 and s[varend - 1] == "$":
                        depth += 1
                    elif s[varend] == "}":
                        depth -= 1
                    varend += 1
                if depth != 0:
                    raise ShellParseError("Bad substitution: " + s[i:])
                varend -= 1
                varname = s[i:varend]
                i = varend
            elif char != "" and _ENV_SPECIAL.match(char):
                varname = char
                i += 1
            else:
                sliced = s[i:]
                end = _ENV_NAME_END.search(sliced)
                if end is None:
                    varname = sliced
                    i = len(s)
                else:
                    varname = sliced[:end.start()]
                    i += end.start() - 1
            if env is not None:
                return env(varname)
            return "" if varname != "" else "$"

        while i < len(s):
            c = s[i]
            is_glob = is_glob or (not quote and c in ("*", "?"))
            if esc:
                out += c
                esc = False
            elif quote:
                if c == quote:
                    quote = ""
                elif quote == "'":
                    out += c
                elif c == "\\":
                    i += 1
                    c = s[i] if i < len(s) else ""
                    if c in ('"', "\\", "$"):
                        out += c
                    else:
                        out += "\\" + c
                elif c == "$":
                    out += parse_env_var()
                else:
                    out += c
            elif c in ('"', "'"):
                quote = c
            elif _CONTROL_RE.match(c):
                result = [{"op": s}]
                break
            elif c == "#":
                commented = True
                comment: dict[str, str] = {"comment": string[match.start() + i + 1:]}
                result = [out, comment] if out else [comment]
                break
            elif c == "\\":
                esc = True
            elif c == "$":
                out += parse_env_var()
            else:
                out += c
            i += 1

        if result is not None:
            out_entries.extend(result)
        elif is_glob:
            out_entries.append({"op": "glob", "pattern": out})
        else:
            out_entries.append(out)

    return out_entries


@dataclass(frozen=True, slots=True)
class ShellParseResult:
    success: bool
    tokens: list[ParseEntry] = field(default_factory=list)
    error: str = ""


def try_parse_shell_command(cmd: str, env: ShellEnv | None = None) -> ShellParseResult:
    """`shellQuote.ts` -> `tryParseShellCommand`."""
    try:
        return ShellParseResult(success=True, tokens=shell_quote_parse(cmd, env))
    except Exception as exc:
        return ShellParseResult(success=False, error=str(exc) or "Unknown parse error")


_UNESCAPED_DQ = re.compile(r'(?<!\\)"')
_UNESCAPED_SQ = re.compile(r"(?<!\\)'")


def has_malformed_tokens(command: str, parsed: list[ParseEntry]) -> bool:
    """`shellQuote.ts` -> `hasMalformedTokens`."""
    in_single = False
    in_double = False
    double_count = 0
    single_count = 0
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single:
            i += 2
            continue
        if c == '"' and not in_single:
            double_count += 1
            in_double = not in_double
        elif c == "'" and not in_double:
            single_count += 1
            in_single = not in_single
        i += 1
    if double_count % 2 != 0 or single_count % 2 != 0:
        return True

    for entry in parsed:
        if not isinstance(entry, str):
            continue
        if entry.count("{") != entry.count("}"):
            return True
        if entry.count("(") != entry.count(")"):
            return True
        if entry.count("[") != entry.count("]"):
            return True
        if len(_UNESCAPED_DQ.findall(entry)) % 2 != 0:
            return True
        if len(_UNESCAPED_SQ.findall(entry)) % 2 != 0:
            return True
    return False


def has_shell_quote_single_quote_bug(command: str) -> bool:
    """`shellQuote.ts` -> `hasShellQuoteSingleQuoteBug`."""
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single:
            i += 2
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            if not in_single:
                backslashes = 0
                j = i - 1
                while j >= 0 and command[j] == "\\":
                    backslashes += 1
                    j -= 1
                if backslashes > 0 and backslashes % 2 == 1:
                    return True
                if backslashes > 0 and backslashes % 2 == 0 and command.find("'", i + 1) != -1:
                    return True
        i += 1
    return False


# ── heredoc.ts -> extractHeredocs ─────────────────────────────────────────────

HEREDOC_START_PATTERN = re.compile(
    r"(?<!<)<<(?!<)(-)?[ \t]*(?:(['\"])(\\?[A-Za-z0-9_]+)\2|\\?([A-Za-z0-9_]+))"
)
_HEREDOC_WORD_TERMINATOR = frozenset(" \t\n|&;()<>")
_HEREDOC_EOF_TOKENS = frozenset(")}`|&;(<>")


@dataclass(frozen=True, slots=True)
class _HeredocInfo:
    operator_start: int
    operator_end: int
    content_start: int
    content_end: int


def extract_heredocs(command: str, *, quoted_only: bool = False) -> str:
    """
    `heredoc.ts` -> `extractHeredocs`, returning only `processedCommand`.

    When extraction is unsafe the command is returned unchanged — the safe direction,
    since the raw text then goes through every validator.
    """
    return extract_heredocs_with_map(command, quoted_only=quoted_only)[0]


def restore_heredocs(parts: list[str], heredocs: dict[str, str]) -> list[str]:
    """`heredoc.ts` -> `restoreHeredocs`: put each placeholder's original text back."""
    if not heredocs:
        return parts
    restored: list[str] = []
    for part in parts:
        for placeholder, full_text in heredocs.items():
            part = part.replace(placeholder, full_text)
        restored.append(part)
    return restored


def extract_heredocs_with_map(command: str, *, quoted_only: bool = False) -> tuple[str, dict[str, str]]:
    """
    `heredoc.ts` -> `extractHeredocs`: (processedCommand, placeholder -> fullText).

    `fullText` is the operator plus the content from the newline through the closing
    delimiter — upstream's normalised form for `restoreHeredocs`.
    """
    unchanged: tuple[str, dict[str, str]] = (command, {})
    if "<<" not in command:
        return unchanged
    if re.search(r"\$['\"]", command):
        return unchanged
    first = command.find("<<")
    if first > 0 and "`" in command[:first]:
        return unchanged
    if first > 0:
        before = command[:first]
        if len(re.findall(r"\(\(", before)) > len(re.findall(r"\)\)", before)):
            return unchanged

    matches: list[_HeredocInfo] = []
    skipped: list[tuple[int, int]] = []

    scan_pos = 0
    in_sq = False
    in_dq = False
    in_comment = False
    dq_escape_next = False
    pending_backslashes = 0

    for m in HEREDOC_START_PATTERN.finditer(command):
        start = m.start()
        for k in range(scan_pos, start):
            ch = command[k]
            if ch == "\n":
                in_comment = False
            if in_sq:
                if ch == "'":
                    in_sq = False
                continue
            if in_dq:
                if dq_escape_next:
                    dq_escape_next = False
                    continue
                if ch == "\\":
                    dq_escape_next = True
                    continue
                if ch == '"':
                    in_dq = False
                continue
            if ch == "\\":
                pending_backslashes += 1
                continue
            escaped = pending_backslashes % 2 == 1
            pending_backslashes = 0
            if escaped:
                continue
            if ch == "'":
                in_sq = True
            elif ch == '"':
                in_dq = True
            elif not in_comment and ch == "#":
                in_comment = True
        scan_pos = max(scan_pos, start)

        if in_sq or in_dq or in_comment:
            continue
        if pending_backslashes % 2 == 1:
            continue
        if any(s_start < start < s_end for s_start, s_end in skipped):
            continue

        full = m.group(0)
        is_dash = m.group(1) == "-"
        delimiter = m.group(3) or m.group(4) or ""
        operator_end = start + len(full)

        quote_char = m.group(2)
        if quote_char and command[operator_end - 1] != quote_char:
            continue
        is_quoted_or_escaped = bool(quote_char) or "\\" in full

        if operator_end < len(command) and command[operator_end] not in _HEREDOC_WORD_TERMINATOR:
            continue

        first_newline = -1
        lsq = False
        ldq = False
        k = operator_end
        while k < len(command):
            ch = command[k]
            if lsq:
                if ch == "'":
                    lsq = False
                k += 1
                continue
            if ldq:
                if ch == "\\":
                    k += 2
                    continue
                if ch == '"':
                    ldq = False
                k += 1
                continue
            if ch == "\n":
                first_newline = k - operator_end
                break
            bs = 0
            j = k - 1
            while j >= operator_end and command[j] == "\\":
                bs += 1
                j -= 1
            if bs % 2 == 1:
                k += 1
                continue
            if ch == "'":
                lsq = True
            elif ch == '"':
                ldq = True
            k += 1
        if first_newline == -1:
            continue

        same_line = command[operator_end:operator_end + first_newline]
        trailing = len(same_line) - len(same_line.rstrip("\\"))
        if trailing % 2 == 1:
            continue

        content_start = operator_end + first_newline
        lines = command[content_start + 1:].split("\n")
        closing = -1
        for idx, line in enumerate(lines):
            check = line.lstrip("\t") if is_dash else line
            if check == delimiter:
                closing = idx
                break
            if len(check) > len(delimiter) and check.startswith(delimiter):
                if check[len(delimiter)] in _HEREDOC_EOF_TOKENS:
                    closing = -1
                    break

        if quoted_only and not is_quoted_or_escaped:
            if closing == -1:
                skip_end = len(command)
            else:
                skip_end = content_start + 1 + len("\n".join(lines[:closing + 1]))
            skipped.append((content_start, skip_end))
            continue
        if closing == -1:
            continue

        content_end = content_start + 1 + len("\n".join(lines[:closing + 1]))
        if any(content_start < s_end and s_start < content_end for s_start, s_end in skipped):
            continue
        matches.append(_HeredocInfo(start, operator_end, content_start, content_end))

    if not matches:
        return unchanged
    top = [
        c for c in matches
        if not any(o is not c and o.content_start < c.operator_start < o.content_end for o in matches)
    ]
    if not top:
        return unchanged
    if len({h.content_start for h in top}) < len(top):
        return unchanged

    top.sort(key=lambda h: h.content_end, reverse=True)
    salt = secrets.token_hex(8)
    processed = command
    heredocs: dict[str, str] = {}
    for index, info in enumerate(top):
        placeholder = f"__HEREDOC_{len(top) - 1 - index}_{salt}__"
        heredocs[placeholder] = (
            command[info.operator_start:info.operator_end] + command[info.content_start:info.content_end]
        )
        processed = (
            processed[:info.operator_start]
            + placeholder
            + processed[info.operator_end:info.content_start]
            + processed[info.content_end:]
        )
    return processed, heredocs


# ── Quote extraction and the validation context ──────────────────────────────

@dataclass(frozen=True, slots=True)
class ValidationContext:
    original_command: str
    base_command: str
    unquoted_content: str
    fully_unquoted_content: str
    fully_unquoted_pre_strip: str
    unquoted_keep_quote_chars: str


def extract_quoted_content(command: str, is_jq: bool = False) -> tuple[str, str, str]:
    """`extractQuotedContent` -> (withDoubleQuotes, fullyUnquoted, unquotedKeepQuoteChars)."""
    with_dq: list[str] = []
    fully: list[str] = []
    keep: list[str] = []
    in_sq = False
    in_dq = False
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            if not in_sq:
                with_dq.append(char)
            if not in_sq and not in_dq:
                fully.append(char)
                keep.append(char)
            continue
        if char == "\\" and not in_sq:
            escaped = True
            with_dq.append(char)
            if not in_dq:
                fully.append(char)
                keep.append(char)
            continue
        if char == "'" and not in_dq:
            in_sq = not in_sq
            keep.append(char)
            continue
        if char == '"' and not in_sq:
            in_dq = not in_dq
            keep.append(char)
            if not is_jq:
                continue
        if not in_sq:
            with_dq.append(char)
        if not in_sq and not in_dq:
            fully.append(char)
            keep.append(char)
    return "".join(with_dq), "".join(fully), "".join(keep)


_END = "(?=" + _WS + r"|\Z)"
_SAFE_REDIRECT_PATTERNS = (
    re.compile(_WS + r"+2" + _WS + r"*>&" + _WS + r"*1" + _END),
    re.compile(r"[012]?" + _WS + r"*>" + _WS + r"*/dev/null" + _END),
    re.compile(_WS + r"*<" + _WS + r"*/dev/null" + _END),
)


def strip_safe_redirections(content: str) -> str:
    """`stripSafeRedirections`. Every pattern keeps its trailing boundary, on purpose."""
    for pattern in _SAFE_REDIRECT_PATTERNS:
        content = pattern.sub("", content)
    return content


def has_unescaped_char(content: str, char: str) -> bool:
    """`hasUnescapedChar` — single characters only."""
    if len(char) != 1:
        raise ValueError("hasUnescapedChar only works with single characters")
    i = 0
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content):
            i += 2
            continue
        if content[i] == char:
            return True
        i += 1
    return False


# ── Early validators ─────────────────────────────────────────────────────────

def validate_empty(ctx: ValidationContext) -> PermissionResult:
    if not _js_trim(ctx.original_command):
        return _allow(ctx.original_command, "Empty command is safe")
    return _passthrough("Command is not empty")


def validate_incomplete_commands(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    trimmed = _js_trim(original)
    if re.match(_WS + r"*\t", original):
        return _ask("Command appears to be an incomplete fragment (starts with tab)")
    if trimmed.startswith("-"):
        return _ask("Command appears to be an incomplete fragment (starts with flags)")
    if re.match(_WS + r"*(&&|\|\||;|>>?|<)", original):
        return _ask("Command appears to be a continuation line (starts with operator)")
    return _passthrough("Command appears complete")


def is_safe_heredoc(command: str) -> bool:
    """`isSafeHeredoc` — the provably-safe `$(cat <<'DELIM' ... DELIM)` early-allow path."""
    if not HEREDOC_IN_SUBSTITUTION.search(command):
        return False

    safe: list[tuple[int, int, str, bool]] = []
    for m in _SAFE_HEREDOC_PATTERN.finditer(command):
        delimiter = m.group(2) or m.group(3)
        if delimiter:
            safe.append((m.start(), m.end(), delimiter, m.group(1) == "-"))
    if not safe:
        return False

    verified: list[tuple[int, int]] = []
    for start, operator_end, delimiter, is_dash in safe:
        after = command[operator_end:]
        open_line_end = after.find("\n")
        if open_line_end == -1:
            return False
        if not re.fullmatch(r"[ \t]*", after[:open_line_end]):
            return False

        body_start = operator_end + open_line_end + 1
        body_lines = command[body_start:].split("\n")
        closing_idx = -1
        paren_line_idx = -1
        paren_col_idx = -1
        for i, raw_line in enumerate(body_lines):
            line = raw_line.lstrip("\t") if is_dash else raw_line
            if line == delimiter:
                closing_idx = i
                if i + 1 >= len(body_lines):
                    return False
                paren = re.match(r"([ \t]*)\)", body_lines[i + 1])
                if not paren:
                    return False
                paren_line_idx = i + 1
                paren_col_idx = len(paren.group(1))
                break
            if line.startswith(delimiter):
                after_delim = line[len(delimiter):]
                paren = re.match(r"([ \t]*)\)", after_delim)
                if paren:
                    closing_idx = i
                    paren_line_idx = i
                    tab_prefix = len(raw_line) - len(raw_line.lstrip("\t")) if is_dash else 0
                    paren_col_idx = tab_prefix + len(delimiter) + len(paren.group(1))
                    break
                if re.match(r"[)}`|&;(<>]", after_delim):
                    return False
        if closing_idx == -1:
            return False

        end_pos = body_start
        for i in range(paren_line_idx):
            end_pos += len(body_lines[i]) + 1
        end_pos += paren_col_idx + 1
        verified.append((start, end_pos))

    for oi, outer in enumerate(verified):
        for ii, inner in enumerate(verified):
            if ii != oi and outer[0] < inner[0] < outer[1]:
                return False

    remaining = command
    for start, end in sorted(verified, key=lambda v: v[0], reverse=True):
        remaining = remaining[:start] + remaining[end:]

    if _js_trim(remaining):
        first_start = min(v[0] for v in verified)
        if not _js_trim(command[:first_start]):
            return False

    if not re.fullmatch(r"[a-zA-Z0-9 \t\"'.\-/_@=,:+~]*", remaining):
        return False

    return bash_command_is_safe(remaining).behavior == PermissionBehavior.PASSTHROUGH


def strip_safe_heredoc_substitutions(command: str) -> str | None:
    """`stripSafeHeredocSubstitutions`: the command with matched heredocs removed, or None."""
    if not HEREDOC_IN_SUBSTITUTION.search(command):
        return None
    ranges: list[tuple[int, int]] = []
    for m in _SAFE_HEREDOC_PATTERN.finditer(command):
        if m.start() > 0 and command[m.start() - 1] == "\\":
            continue
        delimiter = m.group(2) or m.group(3)
        if not delimiter:
            continue
        is_dash = m.group(1) == "-"
        operator_end = m.end()
        after = command[operator_end:]
        open_line_end = after.find("\n")
        if open_line_end == -1:
            continue
        if not re.fullmatch(r"[ \t]*", after[:open_line_end]):
            continue
        body_start = operator_end + open_line_end + 1
        body_lines = command[body_start:].split("\n")
        for i, raw_line in enumerate(body_lines):
            line = raw_line.lstrip("\t") if is_dash else raw_line
            if line.startswith(delimiter):
                rest = line[len(delimiter):]
                close_pos = -1
                if re.match(r"[ \t]*\)", rest):
                    line_start = body_start + len("\n".join(body_lines[:i])) + (1 if i > 0 else 0)
                    close_pos = command.find(")", line_start)
                elif rest == "":
                    if i + 1 < len(body_lines) and re.match(r"[ \t]*\)", body_lines[i + 1]):
                        next_start = body_start + len("\n".join(body_lines[:i + 1])) + 1
                        close_pos = command.find(")", next_start)
                if close_pos != -1:
                    ranges.append((m.start(), close_pos + 1))
                break
    if not ranges:
        return None
    result = command
    for start, end in reversed(ranges):
        result = result[:start] + result[end:]
    return result


def has_safe_heredoc_substitution(command: str) -> bool:
    return strip_safe_heredoc_substitutions(command) is not None


def validate_safe_command_substitution(ctx: ValidationContext) -> PermissionResult:
    if not HEREDOC_IN_SUBSTITUTION.search(ctx.original_command):
        return _passthrough("No heredoc in substitution")
    if is_safe_heredoc(ctx.original_command):
        return _allow(
            ctx.original_command,
            "Safe command substitution: cat with quoted/escaped heredoc delimiter",
        )
    return _passthrough("Command substitution needs validation")


_GIT_COMMIT_MESSAGE = re.compile(
    r"git[ \t]+commit[ \t]+[^;&|`$<>()\n\r]*?-m[ \t]+([\"'])([\s\S]*?)\1(" + _DOT + r"*)\Z"
)


def validate_git_commit(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    if ctx.base_command != "git" or not re.match(r"git" + _WS + r"+commit" + _WS + "+", original):
        return _passthrough("Not a git commit")
    if "\\" in original:
        return _passthrough("Git commit contains backslash, needs full validation")

    m = _GIT_COMMIT_MESSAGE.match(original)
    if m:
        quote, message_content, remainder = m.group(1), m.group(2), m.group(3)
        if quote == '"' and message_content and re.search(r"\$\(|`|\$\{", message_content):
            return _ask("Git commit message contains command substitution patterns")
        if remainder and re.search(r"[;|&()`]|\$\(|\$\{", remainder):
            return _passthrough("Git commit remainder contains shell metacharacters")
        if remainder:
            unquoted: list[str] = []
            in_sq = False
            in_dq = False
            for c in remainder:
                if c == "'" and not in_dq:
                    in_sq = not in_sq
                    continue
                if c == '"' and not in_sq:
                    in_dq = not in_dq
                    continue
                if not in_sq and not in_dq:
                    unquoted.append(c)
            if re.search(r"[<>]", "".join(unquoted)):
                return _passthrough("Git commit remainder contains unquoted redirect operator")
        if message_content and message_content.startswith("-"):
            return _ask("Command contains quoted characters in flag names")
        return _allow(original, "Git commit with simple quoted message is allowed")

    return _passthrough("Git commit needs validation")


# ── Main validators ──────────────────────────────────────────────────────────

def validate_jq_command(ctx: ValidationContext) -> PermissionResult:
    if ctx.base_command != "jq":
        return _passthrough("Not jq")
    if re.search(r"\bsystem" + _WS + r"*\(", ctx.original_command, re.ASCII):
        return _ask("jq command contains system() function which executes arbitrary commands")
    after_jq = _js_trim(ctx.original_command[3:])
    if re.search(
        r"(?:^|" + _WS + r")(?:-f\b|--from-file|--rawfile|--slurpfile|-L\b|--library-path)",
        after_jq,
        re.ASCII,
    ):
        return _ask("jq command contains dangerous flags that could execute code or read arbitrary files")
    return _passthrough("jq command is safe")


_METACHAR_MESSAGE = "Command contains shell metacharacters (;, |, or &) in arguments"
_FIND_GLOB_PATTERNS = tuple(
    re.compile(flag + _WS + r"+[\"'][^\"']*[;|&][^\"']*[\"']") for flag in ("-name", "-path", "-iname")
)


def validate_shell_metacharacters(ctx: ValidationContext) -> PermissionResult:
    content = ctx.unquoted_content
    if re.search(r"(?:^|" + _WS + r")[\"'][^\"']*[;&][^\"']*[\"'](?:" + _WS + r"|\Z)", content):
        return _ask(_METACHAR_MESSAGE)
    if any(p.search(content) for p in _FIND_GLOB_PATTERNS):
        return _ask(_METACHAR_MESSAGE)
    if re.search(r"-regex" + _WS + r"+[\"'][^\"']*[;&][^\"']*[\"']", content):
        return _ask(_METACHAR_MESSAGE)
    return _passthrough("No metacharacters")


def validate_dangerous_variables(ctx: ValidationContext) -> PermissionResult:
    content = ctx.fully_unquoted_content
    if re.search(r"[<>|]" + _WS + r"*\$[A-Za-z_]", content) or re.search(
        r"\$[A-Za-z_][A-Za-z0-9_]*" + _WS + r"*[|<>]", content
    ):
        return _ask("Command contains variables in dangerous contexts (redirections or pipes)")
    return _passthrough("No dangerous variables")


def validate_dangerous_patterns(ctx: ValidationContext) -> PermissionResult:
    content = ctx.unquoted_content
    if has_unescaped_char(content, "`"):
        return _ask("Command contains backticks (`) for command substitution")
    for pattern, message in COMMAND_SUBSTITUTION_PATTERNS:
        if pattern.search(content):
            return _ask(f"Command contains {message}")
    return _passthrough("No dangerous patterns")


def validate_redirections(ctx: ValidationContext) -> PermissionResult:
    content = ctx.fully_unquoted_content
    if "<" in content:
        return _ask("Command contains input redirection (<) which could read sensitive files")
    if ">" in content:
        return _ask("Command contains output redirection (>) which could write to arbitrary files")
    return _passthrough("No redirections")


_NEWLINE_LOOKS_LIKE_COMMAND = re.compile(r"(?<!" + _WS + r"\\)[\n\r]" + _WS + "*" + _NWS)


def validate_newlines(ctx: ValidationContext) -> PermissionResult:
    content = ctx.fully_unquoted_pre_strip
    if "\n" not in content and "\r" not in content:
        return _passthrough("No newlines")
    if _NEWLINE_LOOKS_LIKE_COMMAND.search(content):
        return _ask("Command contains newlines that could separate multiple commands")
    return _passthrough("Newlines appear to be within data")


def validate_carriage_return(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    if "\r" not in original:
        return _passthrough("No carriage return")
    in_sq = False
    in_dq = False
    escaped = False
    for c in original:
        if escaped:
            escaped = False
            continue
        if c == "\\" and not in_sq:
            escaped = True
            continue
        if c == "'" and not in_dq:
            in_sq = not in_sq
            continue
        if c == '"' and not in_sq:
            in_dq = not in_dq
            continue
        if c == "\r" and not in_dq:
            return _ask("Command contains carriage return (\\r) which shell-quote and bash tokenize differently")
    return _passthrough("CR only inside double quotes")


def validate_ifs_injection(ctx: ValidationContext) -> PermissionResult:
    if re.search(r"\$IFS|\$\{[^}]*IFS", ctx.original_command):
        return _ask("Command contains IFS variable usage which could bypass security validation")
    return _passthrough("No IFS injection detected")


def validate_proc_environ_access(ctx: ValidationContext) -> PermissionResult:
    if re.search(r"/proc/" + _DOT + "*/environ", ctx.original_command):
        return _ask("Command accesses /proc/*/environ which could expose sensitive environment variables")
    return _passthrough("No /proc/environ access detected")


def validate_malformed_token_injection(ctx: ValidationContext) -> PermissionResult:
    parsed = try_parse_shell_command(ctx.original_command)
    if not parsed.success:
        return _passthrough("Parse failed, handled elsewhere")
    has_separator = any(
        isinstance(entry, dict) and entry.get("op") in (";", "&&", "||") for entry in parsed.tokens
    )
    if not has_separator:
        return _passthrough("No command separators")
    if has_malformed_tokens(ctx.original_command, parsed.tokens):
        return _ask("Command contains ambiguous syntax with command separators that could be misinterpreted")
    return _passthrough("No malformed token injection detected")


_FLAG_CHARS = re.compile(r"-+[a-zA-Z0-9$`]")
_FLAG_CONTINUATION_CHARS = re.compile(r"[a-zA-Z0-9\\${`-]")
_QUOTED_FLAG_MESSAGE = "Command contains quoted characters in flag names"


def _only_dashes(s: str) -> bool:
    return re.fullmatch(r"-+", s) is not None


def validate_obfuscated_flags(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    base = ctx.base_command

    has_operators = re.search(r"[|&;]", original) is not None
    if base == "echo" and not has_operators:
        return _passthrough("echo command is safe and has no dangerous flags")

    if re.search(r"\$'[^']*'", original):
        return _ask("Command contains ANSI-C quoting which can hide characters")
    if re.search(r'\$"[^"]*"', original):
        return _ask("Command contains locale quoting which can hide characters")
    if re.search(r"\$['\"]{2}" + _WS + r"*-", original):
        return _ask("Command contains empty special quotes before dash (potential bypass)")
    if re.search(r"(?:^|" + _WS + r")(?:''|\"\")+" + _WS + r"*-", original):
        return _ask("Command contains empty quotes before dash (potential bypass)")
    if re.search(r"(?:\"\"|'')+['\"]-", original):
        return _ask("Command contains empty quote pair adjacent to quoted dash (potential flag obfuscation)")
    if re.search(r"(?:^|" + _WS + r")['\"]{3,}", original):
        return _ask("Command contains consecutive quote characters at word start (potential obfuscation)")

    n = len(original)
    in_sq = False
    in_dq = False
    escaped = False
    i = 0
    while i < n - 1:
        current = original[i]
        nxt = original[i + 1]
        if escaped:
            escaped = False
            i += 1
            continue
        if current == "\\" and not in_sq:
            escaped = True
            i += 1
            continue
        if current == "'" and not in_dq:
            in_sq = not in_sq
            i += 1
            continue
        if current == '"' and not in_sq:
            in_dq = not in_dq
            i += 1
            continue
        if in_sq or in_dq:
            i += 1
            continue

        if _is_js_ws(current) and nxt in "'\"`":
            quote_char = nxt
            j = i + 2
            while j < n and original[j] != quote_char:
                j += 1
            inside = original[i + 2:j]
            char_after = original[j + 1] if j + 1 < n else None
            has_inside = _FLAG_CHARS.match(inside) is not None
            has_continuing = (
                _only_dashes(inside)
                and char_after is not None
                and _FLAG_CONTINUATION_CHARS.match(char_after) is not None
            )
            has_next_quote = (
                (inside == "" or _only_dashes(inside))
                and char_after is not None
                and char_after in "'\"`"
                and _flag_in_quote_chain(original, j + 1, inside)
            )
            if j < n and original[j] == quote_char and (has_inside or has_continuing or has_next_quote):
                return _ask(_QUOTED_FLAG_MESSAGE)

        if _is_js_ws(current) and nxt == "-":
            j = i + 1
            flag: list[str] = []
            while j < n:
                flag_char = original[j]
                if flag_char in "=" or _is_js_ws(flag_char):
                    break
                if flag_char in "'\"`":
                    if base == "cut" and "".join(flag) == "-d":
                        break
                    if j + 1 < n and not re.match(r"[a-zA-Z0-9_'\"-]", original[j + 1]):
                        break
                flag.append(flag_char)
                j += 1
            flag_content = "".join(flag)
            if '"' in flag_content or "'" in flag_content:
                return _ask(_QUOTED_FLAG_MESSAGE)
        i += 1

    if re.search(_WS + r"['\"`]-", ctx.fully_unquoted_content):
        return _ask(_QUOTED_FLAG_MESSAGE)
    if re.search(r"['\"`]{2}-", ctx.fully_unquoted_content):
        return _ask(_QUOTED_FLAG_MESSAGE)
    return _passthrough("No obfuscated flags detected")


def _flag_in_quote_chain(original: str, pos: int, inside: str) -> bool:
    """The `hasFlagCharsInNextQuote` closure of `validateObfuscatedFlags`."""
    n = len(original)
    combined = inside
    while pos < n and original[pos] in "'\"`":
        seg_quote = original[pos]
        end = pos + 1
        while end < n and original[end] != seg_quote:
            end += 1
        segment = original[pos + 1:end]
        combined += segment
        if _FLAG_CHARS.match(combined):
            return True
        prior = combined[:-len(segment)] if segment else combined
        if _only_dashes(prior) and re.search(r"[a-zA-Z0-9$`]", segment):
            return True
        if end >= n:
            break
        pos = end + 1
    if pos < n and _FLAG_CONTINUATION_CHARS.match(original[pos]):
        if _only_dashes(combined) or combined == "":
            nxt = original[pos]
            if nxt == "-":
                return True
            if re.match(r"[a-zA-Z0-9\\${`]", nxt) and combined != "":
                return True
        if combined.startswith("-"):
            return True
    return False


def has_backslash_escaped_whitespace(command: str) -> bool:
    in_sq = False
    in_dq = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_sq:
            if not in_dq and i + 1 < len(command) and command[i + 1] in (" ", "\t"):
                return True
            i += 2
            continue
        if char == '"' and not in_sq:
            in_dq = not in_dq
        elif char == "'" and not in_dq:
            in_sq = not in_sq
        i += 1
    return False


def validate_backslash_escaped_whitespace(ctx: ValidationContext) -> PermissionResult:
    if has_backslash_escaped_whitespace(ctx.original_command):
        return _ask("Command contains backslash-escaped whitespace that could alter command parsing")
    return _passthrough("No backslash-escaped whitespace")


def has_backslash_escaped_operator(command: str) -> bool:
    in_sq = False
    in_dq = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_sq:
            if not in_dq and i + 1 < len(command) and command[i + 1] in SHELL_OPERATORS:
                return True
            i += 2
            continue
        if char == "'" and not in_dq:
            in_sq = not in_sq
        elif char == '"' and not in_sq:
            in_dq = not in_dq
        i += 1
    return False


def validate_backslash_escaped_operators(ctx: ValidationContext) -> PermissionResult:
    if has_backslash_escaped_operator(ctx.original_command):
        return _ask(
            "Command contains a backslash before a shell operator (;, |, &, <, >) which can hide command structure"
        )
    return _passthrough("No backslash-escaped operators")


def is_escaped_at_position(content: str, pos: int) -> bool:
    count = 0
    i = pos - 1
    while i >= 0 and content[i] == "\\":
        count += 1
        i -= 1
    return count % 2 == 1


def validate_brace_expansion(ctx: ValidationContext) -> PermissionResult:
    content = ctx.fully_unquoted_pre_strip
    opens = 0
    closes = 0
    for i, ch in enumerate(content):
        if ch == "{" and not is_escaped_at_position(content, i):
            opens += 1
        elif ch == "}" and not is_escaped_at_position(content, i):
            closes += 1
    if opens > 0 and closes > opens:
        return _ask(
            "Command has excess closing braces after quote stripping, "
            "indicating possible brace expansion obfuscation"
        )
    if opens > 0 and re.search(r"['\"][{}]['\"]", ctx.original_command):
        return _ask(
            "Command contains quoted brace character inside brace context "
            "(potential brace expansion obfuscation)"
        )

    for i, ch in enumerate(content):
        if ch != "{" or is_escaped_at_position(content, i):
            continue
        depth = 1
        matching = -1
        for j in range(i + 1, len(content)):
            cj = content[j]
            if cj == "{" and not is_escaped_at_position(content, j):
                depth += 1
            elif cj == "}" and not is_escaped_at_position(content, j):
                depth -= 1
                if depth == 0:
                    matching = j
                    break
        if matching == -1:
            continue
        inner = 0
        for k in range(i + 1, matching):
            ck = content[k]
            if ck == "{" and not is_escaped_at_position(content, k):
                inner += 1
            elif ck == "}" and not is_escaped_at_position(content, k):
                inner -= 1
            elif inner == 0:
                if ck == "," or (ck == "." and k + 1 < matching and content[k + 1] == "."):
                    return _ask("Command contains brace expansion that could alter command parsing")
    return _passthrough("No brace expansion detected")


def validate_unicode_whitespace(ctx: ValidationContext) -> PermissionResult:
    if UNICODE_WS_RE.search(ctx.original_command):
        return _ask("Command contains Unicode whitespace characters that could cause parsing inconsistencies")
    return _passthrough("No Unicode whitespace")


_MID_WORD_HASH = re.compile(_NWS + r"(?<!\$\{)#")


def _join_continuations(match: re.Match[str]) -> str:
    text = match.group(0)
    backslashes = len(text) - 1
    return "\\" * (backslashes - 1) if backslashes % 2 == 1 else text


def validate_mid_word_hash(ctx: ValidationContext) -> PermissionResult:
    content = ctx.unquoted_keep_quote_chars
    joined = re.sub(r"\\+\n", _join_continuations, content)
    if _MID_WORD_HASH.search(content) or _MID_WORD_HASH.search(joined):
        return _ask("Command contains mid-word # which is parsed differently by shell-quote vs bash")
    return _passthrough("No mid-word hash")


def validate_comment_quote_desync(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    in_sq = False
    in_dq = False
    escaped = False
    i = 0
    while i < len(original):
        char = original[i]
        if escaped:
            escaped = False
        elif in_sq:
            if char == "'":
                in_sq = False
        elif char == "\\":
            escaped = True
        elif in_dq:
            if char == '"':
                in_dq = False
        elif char == "'":
            in_sq = True
        elif char == '"':
            in_dq = True
        elif char == "#":
            line_end = original.find("\n", i)
            comment = original[i + 1:] if line_end == -1 else original[i + 1:line_end]
            if "'" in comment or '"' in comment:
                return _ask("Command contains quote characters inside a # comment which can desync quote tracking")
            if line_end == -1:
                break
            i = line_end
        i += 1
    return _passthrough("No comment quote desync")


def validate_quoted_newline(ctx: ValidationContext) -> PermissionResult:
    original = ctx.original_command
    if "\n" not in original or "#" not in original:
        return _passthrough("No newline or no hash")
    in_sq = False
    in_dq = False
    escaped = False
    for i, char in enumerate(original):
        if escaped:
            escaped = False
            continue
        if char == "\\" and not in_sq:
            escaped = True
            continue
        if char == "'" and not in_dq:
            in_sq = not in_sq
            continue
        if char == '"' and not in_sq:
            in_dq = not in_dq
            continue
        if char == "\n" and (in_sq or in_dq):
            next_nl = original.find("\n", i + 1)
            next_line = original[i + 1:] if next_nl == -1 else original[i + 1:next_nl]
            if _js_trim(next_line).startswith("#"):
                return _ask(
                    "Command contains a quoted newline followed by a #-prefixed line, "
                    "which can hide arguments from line-based permission checks"
                )
    return _passthrough("No quoted newline-hash pattern")


def validate_zsh_dangerous_commands(ctx: ValidationContext) -> PermissionResult:
    trimmed = _js_trim(ctx.original_command)
    base = ""
    for token in re.split(_WS + "+", trimmed):
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", token):
            continue
        if token in ZSH_PRECOMMAND_MODIFIERS:
            continue
        base = token
        break
    if base in ZSH_DANGEROUS_COMMANDS:
        return _ask(f"Command uses Zsh-specific '{base}' which can bypass security checks")
    if base == "fc" and re.search(_WS + "-" + _NWS + "*e", trimmed):
        return _ask("Command uses 'fc -e' which can execute arbitrary commands via editor")
    return _passthrough("No Zsh dangerous commands")


# ── bashCommandIsSafe_DEPRECATED ─────────────────────────────────────────────

Validator = Callable[[ValidationContext], PermissionResult]

EARLY_VALIDATORS: tuple[Validator, ...] = (
    validate_empty,
    validate_incomplete_commands,
    validate_safe_command_substitution,
    validate_git_commit,
)

#: Validators whose 'ask' does NOT carry the misparsing flag — LF newlines and redirections
#: are patterns command splitting handles correctly. Their asks are deferred.
NON_MISPARSING_VALIDATORS: frozenset[Validator] = frozenset({validate_newlines, validate_redirections})

VALIDATORS: tuple[Validator, ...] = (
    validate_jq_command,
    validate_obfuscated_flags,
    validate_shell_metacharacters,
    validate_dangerous_variables,
    validate_comment_quote_desync,
    validate_quoted_newline,
    validate_carriage_return,
    validate_newlines,
    validate_ifs_injection,
    validate_proc_environ_access,
    validate_dangerous_patterns,
    validate_redirections,
    validate_backslash_escaped_whitespace,
    validate_backslash_escaped_operators,
    validate_unicode_whitespace,
    validate_mid_word_hash,
    validate_brace_expansion,
    validate_zsh_dangerous_commands,
    validate_malformed_token_injection,
)


def _misparsing(result: PermissionResult) -> PermissionResult:
    return PermissionResult(
        behavior=result.behavior,
        updated_input=result.updated_input,
        message=result.message,
        decision_reason=result.decision_reason,
        is_bash_security_check_for_misparsing=True,
    )


def build_validation_context(command: str) -> ValidationContext:
    processed = extract_heredocs(command, quoted_only=True)
    base = command.split(" ")[0]
    with_dq, fully, keep = extract_quoted_content(processed, base == "jq")
    return ValidationContext(
        original_command=command,
        base_command=base,
        unquoted_content=with_dq,
        fully_unquoted_content=strip_safe_redirections(fully),
        fully_unquoted_pre_strip=fully,
        unquoted_keep_quote_chars=keep,
    )


def bash_command_is_safe(command: str) -> PermissionResult:
    """
    `bashSecurity.ts` -> `bashCommandIsSafe_DEPRECATED`.

    Returns 'passthrough' when the command passed every check, or 'ask' with the first
    validator's reason. Misparsing asks short-circuit; non-misparsing asks are deferred so
    a later misparsing validator still wins (`cat safe.txt \\; echo /etc/passwd > ./out`).
    """
    if CONTROL_CHAR_RE.search(command):
        return _misparsing(_ask(
            "Command contains non-printable control characters that could be used to bypass security checks"
        ))
    if has_shell_quote_single_quote_bug(command):
        return _misparsing(_ask(
            "Command contains single-quoted backslash pattern that could bypass security checks"
        ))

    ctx = build_validation_context(command)

    for early in EARLY_VALIDATORS:
        result = early(ctx)
        if result.behavior == PermissionBehavior.ALLOW:
            reason = (result.decision_reason or {}).get("reason")
            return _passthrough(reason or "Command allowed")
        if result.behavior != PermissionBehavior.PASSTHROUGH:
            return _misparsing(result) if result.behavior == PermissionBehavior.ASK else result

    deferred: PermissionResult | None = None
    for validator in VALIDATORS:
        result = validator(ctx)
        if result.behavior == PermissionBehavior.ASK:
            if validator in NON_MISPARSING_VALIDATORS:
                if deferred is None:
                    deferred = result
                continue
            return _misparsing(result)
    if deferred is not None:
        return deferred
    return _passthrough("Command passed all security checks")


def is_env_truthy(value: str | None) -> bool:
    """`src/utils/envUtils.ts` -> `isEnvTruthy`."""
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


def injection_check_disabled() -> bool:
    """
    Upstream `CLAUDE_CODE_DISABLE_COMMAND_INJECTION_CHECK`, renamed
    `TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK`; read per call as upstream reads `process.env`.
    """
    return is_env_truthy(os.environ.get("TDDAGENTS_DISABLE_COMMAND_INJECTION_CHECK"))


__all__ = [
    "COMMAND_SUBSTITUTION_PATTERNS",
    "CONTROL_CHAR_RE",
    "EARLY_VALIDATORS",
    "NON_MISPARSING_VALIDATORS",
    "ShellParseError",
    "ShellParseResult",
    "VALIDATORS",
    "ValidationContext",
    "ZSH_DANGEROUS_COMMANDS",
    "bash_command_is_safe",
    "build_validation_context",
    "extract_heredocs",
    "extract_quoted_content",
    "has_malformed_tokens",
    "has_safe_heredoc_substitution",
    "has_shell_quote_single_quote_bug",
    "injection_check_disabled",
    "is_env_truthy",
    "shell_quote_parse",
    "strip_safe_heredoc_substitutions",
    "strip_safe_redirections",
    "try_parse_shell_command",
]
