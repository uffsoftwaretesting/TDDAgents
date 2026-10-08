"""
Read-only command tables for the Bash tool: which commands, with which flags, are safe to
auto-allow without a permission rule.

Ported from claude-code v2.1.88:

* `src/utils/shell/readOnlyCommandValidation.ts` -> `FlagArgType`, the `GIT_*` flag groups,
  `GIT_READ_ONLY_COMMANDS`, `ghIsDangerousCallback`, `GH_READ_ONLY_COMMANDS`,
  `DOCKER_READ_ONLY_COMMANDS`, `RIPGREP_READ_ONLY_COMMANDS`, `PYRIGHT_READ_ONLY_COMMANDS`,
  `EXTERNAL_READONLY_COMMANDS`, `containsVulnerableUncPath`, `FLAG_PATTERN`,
  `validateFlagArgument`, `validateFlags`.
* `src/tools/BashTool/readOnlyValidation.ts` -> `FD_SAFE_FLAGS`, `COMMAND_ALLOWLIST`,
  `ANT_ONLY_COMMAND_ALLOWLIST` and their callbacks.

The flag tables were converted mechanically from the TypeScript object literals (same keys,
same values, same order); the `additionalCommandIsDangerousCallback`s are ported by hand,
one function per entry, named after it. JavaScript regex semantics are kept explicit:
`$` is written `\\Z` (JS `$` without the `m` flag never matches before a trailing newline)
and `\\s` is the JS whitespace class.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

FlagArgType = Literal["none", "number", "string", "char", "{}", "EOF"]
DangerCallback = Callable[[str, list[str]], bool]

#: JS `\s` (ECMAScript WhiteSpace + LineTerminator).
JS_WS = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + JS_WS + "]"


@dataclass(frozen=True, slots=True)
class CommandConfig:
    """`ExternalCommandConfig` / `CommandConfig`."""

    safe_flags: Mapping[str, str]
    regex: re.Pattern[str] | None = None
    callback: DangerCallback | None = None
    respects_double_dash: bool = True


def get_platform() -> str:
    """`getPlatform()` reduced to what the read-only checks ask: 'windows' or not."""
    return "windows" if sys.platform.startswith("win") else sys.platform


# ── callbacks (readOnlyCommandValidation.ts) ─────────────────────────────────

def _cb_git_reflog(_raw: str, args: list[str]) -> bool:
    """Only the read-only `show` (or no) subcommand; expire/delete/exists write .git/logs."""
    for token in args:
        if not token or token.startswith("-"):
            continue
        if token in ("expire", "delete", "exists"):
            return True
        return False
    return False


def _cb_git_remote_show(_raw: str, args: list[str]) -> bool:
    positional = [a for a in args if a != "-n"]
    if len(positional) != 1:
        return True
    return re.fullmatch(r"[a-zA-Z0-9_-]+", positional[0]) is None


def _cb_git_remote(_raw: str, args: list[str]) -> bool:
    return any(a not in ("-v", "--verbose") for a in args)


def _cb_git_tag(_raw: str, args: list[str]) -> bool:
    """A positional without `--list`/`-l` creates a tag."""
    flags_with_args = {
        "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "-n",
    }
    i = 0
    seen_list = False
    seen_dash_dash = False
    while i < len(args):
        token = args[i]
        if not token:
            i += 1
            continue
        if token == "--" and not seen_dash_dash:
            seen_dash_dash = True
            i += 1
            continue
        if not seen_dash_dash and token.startswith("-"):
            if token in ("--list", "-l"):
                seen_list = True
            elif token[0] == "-" and token[1:2] != "-" and len(token) > 2 and "=" not in token and "l" in token[1:]:
                seen_list = True
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                i += 2
            else:
                i += 1
        else:
            if not seen_list:
                return True
            i += 1
    return False


def _cb_git_branch(_raw: str, args: list[str]) -> bool:
    """A positional without `--list`/`-l` or a filtering flag creates a branch."""
    flags_with_args = {"--contains", "--no-contains", "--points-at", "--sort"}
    flags_with_optional_args = {"--merged", "--no-merged"}
    i = 0
    last_flag = ""
    seen_list = False
    seen_dash_dash = False
    while i < len(args):
        token = args[i]
        if not token:
            i += 1
            continue
        if token == "--" and not seen_dash_dash:
            seen_dash_dash = True
            last_flag = ""
            i += 1
            continue
        if not seen_dash_dash and token.startswith("-"):
            if token in ("--list", "-l"):
                seen_list = True
            elif token[0] == "-" and token[1:2] != "-" and len(token) > 2 and "=" not in token and "l" in token[1:]:
                seen_list = True
            if "=" in token:
                last_flag = token.split("=")[0] or ""
                i += 1
            elif token in flags_with_args:
                last_flag = token
                i += 2
            else:
                last_flag = token
                i += 1
        else:
            if not seen_list and last_flag not in flags_with_optional_args:
                return True
            i += 1
    return False


def gh_is_dangerous_callback(_raw: str, args: list[str]) -> bool:
    """`ghIsDangerousCallback`: no URLs, `user@host`, or `HOST/OWNER/REPO` targets."""
    for token in args:
        if not token:
            continue
        value = token
        if token.startswith("-"):
            eq = token.find("=")
            if eq == -1:
                continue
            value = token[eq + 1:]
            if not value:
                continue
        if "/" not in value and "://" not in value and "@" not in value:
            continue
        if "://" in value:
            return True
        if "@" in value:
            return True
        if value.count("/") >= 2:
            return True
    return False


def _cb_pyright(_raw: str, args: list[str]) -> bool:
    return any(t in ("--watch", "-w") for t in args)


# ── callbacks (readOnlyValidation.ts) ────────────────────────────────────────

def _cb_sed(raw: str, _args: list[str]) -> bool:
    from app.loop.permissions.bash_sed_validation import sed_command_is_allowed_by_allowlist

    return not sed_command_is_allowed_by_allowlist(raw)


def _cb_ps(_raw: str, args: list[str]) -> bool:
    """BSD-style `e` (environment of every process) in a bare-letter argument."""
    return any(not a.startswith("-") and re.fullmatch(r"[a-zA-Z]*e[a-zA-Z]*", a) is not None for a in args)


def _cb_date(_raw: str, args: list[str]) -> bool:
    """A positional that is not a `+FORMAT` would set the system date."""
    flags_with_args = {"-d", "--date", "-r", "--reference", "--iso-8601", "--rfc-3339"}
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--") and "=" in token:
            i += 1
        elif token.startswith("-"):
            i += 2 if token in flags_with_args else 1
        else:
            if not token.startswith("+"):
                return True
            i += 1
    return False


def _cb_lsof(_raw: str, args: list[str]) -> bool:
    return any(a == "+m" or a.startswith("+m") for a in args)


_TPUT_DANGEROUS_CAPABILITIES = frozenset({
    "init", "reset", "rs1", "rs2", "rs3", "is1", "is2", "is3", "iprog", "if", "rf", "clear", "flash",
    "mc0", "mc4", "mc5", "mc5i", "mc5p", "pfkey", "pfloc", "pfx", "pfxl", "smcup", "rmcup",
})


def _cb_tput(_raw: str, args: list[str]) -> bool:
    i = 0
    after_dash_dash = False
    while i < len(args):
        token = args[i]
        if token == "--":
            after_dash_dash = True
            i += 1
        elif not after_dash_dash and token.startswith("-"):
            if token == "-S":
                return True
            if not token.startswith("--") and len(token) > 2 and "S" in token:
                return True
            i += 2 if token == "-T" else 1
        else:
            if token in _TPUT_DANGEROUS_CAPABILITIES:
                return True
            i += 1
    return False


CALLBACKS: dict[str, DangerCallback] = {
    "git reflog": _cb_git_reflog,
    "git remote show": _cb_git_remote_show,
    "git remote": _cb_git_remote,
    "git tag": _cb_git_tag,
    "git branch": _cb_git_branch,
    "pyright": _cb_pyright,
    "sed": _cb_sed,
    "ps": _cb_ps,
    "date": _cb_date,
    "lsof": _cb_lsof,
    "tput": _cb_tput,
}

REGEXES: dict[str, re.Pattern[str]] = {
    "hostname": re.compile("^hostname(?:" + _S + "+(?:-[a-zA-Z]|--[a-zA-Z-]+))*" + _S + r"*\Z"),
}


# ── tables (converted) ───────────────────────────────────────────────────────

GIT_REF_SELECTION_FLAGS: dict[str, Any] = {
  "--all": "none",
  "--branches": "none",
  "--tags": "none",
  "--remotes": "none",
}


GIT_DATE_FILTER_FLAGS: dict[str, Any] = {
  "--since": "string",
  "--after": "string",
  "--until": "string",
  "--before": "string",
}


GIT_LOG_DISPLAY_FLAGS: dict[str, Any] = {
  "--oneline": "none",
  "--graph": "none",
  "--decorate": "none",
  "--no-decorate": "none",
  "--date": "string",
  "--relative-date": "none",
}


GIT_COUNT_FLAGS: dict[str, Any] = {
  "--max-count": "number",
  "-n": "number",
}


GIT_STAT_FLAGS: dict[str, Any] = {
  "--stat": "none",
  "--numstat": "none",
  "--shortstat": "none",
  "--name-only": "none",
  "--name-status": "none",
}


GIT_COLOR_FLAGS: dict[str, Any] = {
  "--color": "none",
  "--no-color": "none",
}


GIT_PATCH_FLAGS: dict[str, Any] = {
  "--patch": "none",
  "-p": "none",
  "--no-patch": "none",
  "--no-ext-diff": "none",
  "-s": "none",
}


GIT_AUTHOR_FILTER_FLAGS: dict[str, Any] = {
  "--author": "string",
  "--committer": "string",
  "--grep": "string",
}


GIT_READ_ONLY_COMMANDS: dict[str, Any] = {
  "git diff": {
    "safe_flags": {
      **GIT_STAT_FLAGS,
      **GIT_COLOR_FLAGS,
      "--dirstat": "none",
      "--summary": "none",
      "--patch-with-stat": "none",
      "--word-diff": "none",
      "--word-diff-regex": "string",
      "--color-words": "none",
      "--no-renames": "none",
      "--no-ext-diff": "none",
      "--check": "none",
      "--ws-error-highlight": "string",
      "--full-index": "none",
      "--binary": "none",
      "--abbrev": "number",
      "--break-rewrites": "none",
      "--find-renames": "none",
      "--find-copies": "none",
      "--find-copies-harder": "none",
      "--irreversible-delete": "none",
      "--diff-algorithm": "string",
      "--histogram": "none",
      "--patience": "none",
      "--minimal": "none",
      "--ignore-space-at-eol": "none",
      "--ignore-space-change": "none",
      "--ignore-all-space": "none",
      "--ignore-blank-lines": "none",
      "--inter-hunk-context": "number",
      "--function-context": "none",
      "--exit-code": "none",
      "--quiet": "none",
      "--cached": "none",
      "--staged": "none",
      "--pickaxe-regex": "none",
      "--pickaxe-all": "none",
      "--no-index": "none",
      "--relative": "string",
      "--diff-filter": "string",
      "-p": "none",
      "-u": "none",
      "-s": "none",
      "-M": "none",
      "-C": "none",
      "-B": "none",
      "-D": "none",
      "-l": "none",
      "-S": "string",
      "-G": "string",
      "-O": "string",
      "-R": "none",
    },
  },
  "git log": {
    "safe_flags": {
      **GIT_LOG_DISPLAY_FLAGS,
      **GIT_REF_SELECTION_FLAGS,
      **GIT_DATE_FILTER_FLAGS,
      **GIT_COUNT_FLAGS,
      **GIT_STAT_FLAGS,
      **GIT_COLOR_FLAGS,
      **GIT_PATCH_FLAGS,
      **GIT_AUTHOR_FILTER_FLAGS,
      "--abbrev-commit": "none",
      "--full-history": "none",
      "--dense": "none",
      "--sparse": "none",
      "--simplify-merges": "none",
      "--ancestry-path": "none",
      "--source": "none",
      "--first-parent": "none",
      "--merges": "none",
      "--no-merges": "none",
      "--reverse": "none",
      "--walk-reflogs": "none",
      "--skip": "number",
      "--max-age": "number",
      "--min-age": "number",
      "--no-min-parents": "none",
      "--no-max-parents": "none",
      "--follow": "none",
      "--no-walk": "none",
      "--left-right": "none",
      "--cherry-mark": "none",
      "--cherry-pick": "none",
      "--boundary": "none",
      "--topo-order": "none",
      "--date-order": "none",
      "--author-date-order": "none",
      "--pretty": "string",
      "--format": "string",
      "--diff-filter": "string",
      "-S": "string",
      "-G": "string",
      "--pickaxe-regex": "none",
      "--pickaxe-all": "none",
    },
  },
  "git show": {
    "safe_flags": {
      **GIT_LOG_DISPLAY_FLAGS,
      **GIT_STAT_FLAGS,
      **GIT_COLOR_FLAGS,
      **GIT_PATCH_FLAGS,
      "--abbrev-commit": "none",
      "--word-diff": "none",
      "--word-diff-regex": "string",
      "--color-words": "none",
      "--pretty": "string",
      "--format": "string",
      "--first-parent": "none",
      "--raw": "none",
      "--diff-filter": "string",
      "-m": "none",
      "--quiet": "none",
    },
  },
  "git shortlog": {
    "safe_flags": {
      **GIT_REF_SELECTION_FLAGS,
      **GIT_DATE_FILTER_FLAGS,
      "-s": "none",
      "--summary": "none",
      "-n": "none",
      "--numbered": "none",
      "-e": "none",
      "--email": "none",
      "-c": "none",
      "--committer": "none",
      "--group": "string",
      "--format": "string",
      "--no-merges": "none",
      "--author": "string",
    },
  },
  "git reflog": {
    "safe_flags": {
      **GIT_LOG_DISPLAY_FLAGS,
      **GIT_REF_SELECTION_FLAGS,
      **GIT_DATE_FILTER_FLAGS,
      **GIT_COUNT_FLAGS,
      **GIT_AUTHOR_FILTER_FLAGS,
    },
    "callback": CALLBACKS['git reflog'],
  },
  "git stash list": {
    "safe_flags": {
      **GIT_LOG_DISPLAY_FLAGS,
      **GIT_REF_SELECTION_FLAGS,
      **GIT_COUNT_FLAGS,
    },
  },
  "git ls-remote": {
    "safe_flags": {
      "--branches": "none",
      "-b": "none",
      "--tags": "none",
      "-t": "none",
      "--heads": "none",
      "-h": "none",
      "--refs": "none",
      "--quiet": "none",
      "-q": "none",
      "--exit-code": "none",
      "--get-url": "none",
      "--symref": "none",
      "--sort": "string",
    },
  },
  "git status": {
    "safe_flags": {
      "--short": "none",
      "-s": "none",
      "--branch": "none",
      "-b": "none",
      "--porcelain": "none",
      "--long": "none",
      "--verbose": "none",
      "-v": "none",
      "--untracked-files": "string",
      "-u": "string",
      "--ignored": "none",
      "--ignore-submodules": "string",
      "--column": "none",
      "--no-column": "none",
      "--ahead-behind": "none",
      "--no-ahead-behind": "none",
      "--renames": "none",
      "--no-renames": "none",
      "--find-renames": "string",
      "-M": "string",
    },
  },
  "git blame": {
    "safe_flags": {
      **GIT_COLOR_FLAGS,
      "-L": "string",
      "--porcelain": "none",
      "-p": "none",
      "--line-porcelain": "none",
      "--incremental": "none",
      "--root": "none",
      "--show-stats": "none",
      "--show-name": "none",
      "--show-number": "none",
      "-n": "none",
      "--show-email": "none",
      "-e": "none",
      "-f": "none",
      "--date": "string",
      "-w": "none",
      "--ignore-rev": "string",
      "--ignore-revs-file": "string",
      "-M": "none",
      "-C": "none",
      "--score-debug": "none",
      "--abbrev": "number",
      "-s": "none",
      "-l": "none",
      "-t": "none",
    },
  },
  "git ls-files": {
    "safe_flags": {
      "--cached": "none",
      "-c": "none",
      "--deleted": "none",
      "-d": "none",
      "--modified": "none",
      "-m": "none",
      "--others": "none",
      "-o": "none",
      "--ignored": "none",
      "-i": "none",
      "--stage": "none",
      "-s": "none",
      "--killed": "none",
      "-k": "none",
      "--unmerged": "none",
      "-u": "none",
      "--directory": "none",
      "--no-empty-directory": "none",
      "--eol": "none",
      "--full-name": "none",
      "--abbrev": "number",
      "--debug": "none",
      "-z": "none",
      "-t": "none",
      "-v": "none",
      "-f": "none",
      "--exclude": "string",
      "-x": "string",
      "--exclude-from": "string",
      "-X": "string",
      "--exclude-per-directory": "string",
      "--exclude-standard": "none",
      "--error-unmatch": "none",
      "--recurse-submodules": "none",
    },
  },
  "git config --get": {
    "safe_flags": {
      "--local": "none",
      "--global": "none",
      "--system": "none",
      "--worktree": "none",
      "--default": "string",
      "--type": "string",
      "--bool": "none",
      "--int": "none",
      "--bool-or-int": "none",
      "--path": "none",
      "--expiry-date": "none",
      "-z": "none",
      "--null": "none",
      "--name-only": "none",
      "--show-origin": "none",
      "--show-scope": "none",
    },
  },
  "git remote show": {
    "safe_flags": {
      "-n": "none",
    },
    "callback": CALLBACKS['git remote show'],
  },
  "git remote": {
    "safe_flags": {
      "-v": "none",
      "--verbose": "none",
    },
    "callback": CALLBACKS['git remote'],
  },
  "git merge-base": {
    "safe_flags": {
      "--is-ancestor": "none",
      "--fork-point": "none",
      "--octopus": "none",
      "--independent": "none",
      "--all": "none",
    },
  },
  "git rev-parse": {
    "safe_flags": {
      "--verify": "none",
      "--short": "string",
      "--abbrev-ref": "none",
      "--symbolic": "none",
      "--symbolic-full-name": "none",
      "--show-toplevel": "none",
      "--show-cdup": "none",
      "--show-prefix": "none",
      "--git-dir": "none",
      "--git-common-dir": "none",
      "--absolute-git-dir": "none",
      "--show-superproject-working-tree": "none",
      "--is-inside-work-tree": "none",
      "--is-inside-git-dir": "none",
      "--is-bare-repository": "none",
      "--is-shallow-repository": "none",
      "--is-shallow-update": "none",
      "--path-prefix": "none",
    },
  },
  "git rev-list": {
    "safe_flags": {
      **GIT_REF_SELECTION_FLAGS,
      **GIT_DATE_FILTER_FLAGS,
      **GIT_COUNT_FLAGS,
      **GIT_AUTHOR_FILTER_FLAGS,
      "--count": "none",
      "--reverse": "none",
      "--first-parent": "none",
      "--ancestry-path": "none",
      "--merges": "none",
      "--no-merges": "none",
      "--min-parents": "number",
      "--max-parents": "number",
      "--no-min-parents": "none",
      "--no-max-parents": "none",
      "--skip": "number",
      "--max-age": "number",
      "--min-age": "number",
      "--walk-reflogs": "none",
      "--oneline": "none",
      "--abbrev-commit": "none",
      "--pretty": "string",
      "--format": "string",
      "--abbrev": "number",
      "--full-history": "none",
      "--dense": "none",
      "--sparse": "none",
      "--source": "none",
      "--graph": "none",
    },
  },
  "git describe": {
    "safe_flags": {
      "--tags": "none",
      "--match": "string",
      "--exclude": "string",
      "--long": "none",
      "--abbrev": "number",
      "--always": "none",
      "--contains": "none",
      "--first-match": "none",
      "--exact-match": "none",
      "--candidates": "number",
      "--dirty": "none",
      "--broken": "none",
    },
  },
  "git cat-file": {
    "safe_flags": {
      "-t": "none",
      "-s": "none",
      "-p": "none",
      "-e": "none",
      "--batch-check": "none",
      "--allow-undetermined-type": "none",
    },
  },
  "git for-each-ref": {
    "safe_flags": {
      "--format": "string",
      "--sort": "string",
      "--count": "number",
      "--contains": "string",
      "--no-contains": "string",
      "--merged": "string",
      "--no-merged": "string",
      "--points-at": "string",
    },
  },
  "git grep": {
    "safe_flags": {
      "-e": "string",
      "-E": "none",
      "--extended-regexp": "none",
      "-G": "none",
      "--basic-regexp": "none",
      "-F": "none",
      "--fixed-strings": "none",
      "-P": "none",
      "--perl-regexp": "none",
      "-i": "none",
      "--ignore-case": "none",
      "-v": "none",
      "--invert-match": "none",
      "-w": "none",
      "--word-regexp": "none",
      "-n": "none",
      "--line-number": "none",
      "-c": "none",
      "--count": "none",
      "-l": "none",
      "--files-with-matches": "none",
      "-L": "none",
      "--files-without-match": "none",
      "-h": "none",
      "-H": "none",
      "--heading": "none",
      "--break": "none",
      "--full-name": "none",
      "--color": "none",
      "--no-color": "none",
      "-o": "none",
      "--only-matching": "none",
      "-A": "number",
      "--after-context": "number",
      "-B": "number",
      "--before-context": "number",
      "-C": "number",
      "--context": "number",
      "--and": "none",
      "--or": "none",
      "--not": "none",
      "--max-depth": "number",
      "--untracked": "none",
      "--no-index": "none",
      "--recurse-submodules": "none",
      "--cached": "none",
      "--threads": "number",
      "-q": "none",
      "--quiet": "none",
    },
  },
  "git stash show": {
    "safe_flags": {
      **GIT_STAT_FLAGS,
      **GIT_COLOR_FLAGS,
      **GIT_PATCH_FLAGS,
      "--word-diff": "none",
      "--word-diff-regex": "string",
      "--diff-filter": "string",
      "--abbrev": "number",
    },
  },
  "git worktree list": {
    "safe_flags": {
      "--porcelain": "none",
      "-v": "none",
      "--verbose": "none",
      "--expire": "string",
    },
  },
  "git tag": {
    "safe_flags": {
      "-l": "none",
      "--list": "none",
      "-n": "number",
      "--contains": "string",
      "--no-contains": "string",
      "--merged": "string",
      "--no-merged": "string",
      "--sort": "string",
      "--format": "string",
      "--points-at": "string",
      "--column": "none",
      "--no-column": "none",
      "-i": "none",
      "--ignore-case": "none",
    },
    "callback": CALLBACKS['git tag'],
  },
  "git branch": {
    "safe_flags": {
      "-l": "none",
      "--list": "none",
      "-a": "none",
      "--all": "none",
      "-r": "none",
      "--remotes": "none",
      "-v": "none",
      "-vv": "none",
      "--verbose": "none",
      "--color": "none",
      "--no-color": "none",
      "--column": "none",
      "--no-column": "none",
      "--abbrev": "number",
      "--no-abbrev": "none",
      "--contains": "string",
      "--no-contains": "string",
      "--merged": "none",
      "--no-merged": "none",
      "--points-at": "string",
      "--sort": "string",
      "--show-current": "none",
      "-i": "none",
      "--ignore-case": "none",
    },
    "callback": CALLBACKS['git branch'],
  },
}


GH_READ_ONLY_COMMANDS: dict[str, Any] = {
  "gh pr view": {
    "safe_flags": {
      "--json": "string",
      "--comments": "none",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh pr list": {
    "safe_flags": {
      "--state": "string",
      "-s": "string",
      "--author": "string",
      "--assignee": "string",
      "--label": "string",
      "--limit": "number",
      "-L": "number",
      "--base": "string",
      "--head": "string",
      "--search": "string",
      "--json": "string",
      "--draft": "none",
      "--app": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh pr diff": {
    "safe_flags": {
      "--color": "string",
      "--name-only": "none",
      "--patch": "none",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh pr checks": {
    "safe_flags": {
      "--watch": "none",
      "--required": "none",
      "--fail-fast": "none",
      "--json": "string",
      "--interval": "number",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh issue view": {
    "safe_flags": {
      "--json": "string",
      "--comments": "none",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh issue list": {
    "safe_flags": {
      "--state": "string",
      "-s": "string",
      "--assignee": "string",
      "--author": "string",
      "--label": "string",
      "--limit": "number",
      "-L": "number",
      "--milestone": "string",
      "--search": "string",
      "--json": "string",
      "--app": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh repo view": {
    "safe_flags": {
      "--json": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh run list": {
    "safe_flags": {
      "--branch": "string",
      "-b": "string",
      "--status": "string",
      "-s": "string",
      "--workflow": "string",
      "-w": "string",
      "--limit": "number",
      "-L": "number",
      "--json": "string",
      "--repo": "string",
      "-R": "string",
      "--event": "string",
      "-e": "string",
      "--user": "string",
      "-u": "string",
      "--created": "string",
      "--commit": "string",
      "-c": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh run view": {
    "safe_flags": {
      "--log": "none",
      "--log-failed": "none",
      "--exit-status": "none",
      "--verbose": "none",
      "-v": "none",
      "--json": "string",
      "--repo": "string",
      "-R": "string",
      "--job": "string",
      "-j": "string",
      "--attempt": "number",
      "-a": "number",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh auth status": {
    "safe_flags": {
      "--active": "none",
      "-a": "none",
      "--hostname": "string",
      "-h": "string",
      "--json": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh pr status": {
    "safe_flags": {
      "--conflict-status": "none",
      "-c": "none",
      "--json": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh issue status": {
    "safe_flags": {
      "--json": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh release list": {
    "safe_flags": {
      "--exclude-drafts": "none",
      "--exclude-pre-releases": "none",
      "--json": "string",
      "--limit": "number",
      "-L": "number",
      "--order": "string",
      "-O": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh release view": {
    "safe_flags": {
      "--json": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh workflow list": {
    "safe_flags": {
      "--all": "none",
      "-a": "none",
      "--json": "string",
      "--limit": "number",
      "-L": "number",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh workflow view": {
    "safe_flags": {
      "--ref": "string",
      "-r": "string",
      "--yaml": "none",
      "-y": "none",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh label list": {
    "safe_flags": {
      "--json": "string",
      "--limit": "number",
      "-L": "number",
      "--order": "string",
      "--search": "string",
      "-S": "string",
      "--sort": "string",
      "--repo": "string",
      "-R": "string",
    },
    "callback": gh_is_dangerous_callback,
  },
  "gh search repos": {
    "safe_flags": {
      "--archived": "none",
      "--created": "string",
      "--followers": "string",
      "--forks": "string",
      "--good-first-issues": "string",
      "--help-wanted-issues": "string",
      "--include-forks": "string",
      "--json": "string",
      "--language": "string",
      "--license": "string",
      "--limit": "number",
      "-L": "number",
      "--match": "string",
      "--number-topics": "string",
      "--order": "string",
      "--owner": "string",
      "--size": "string",
      "--sort": "string",
      "--stars": "string",
      "--topic": "string",
      "--updated": "string",
      "--visibility": "string",
    },
  },
  "gh search issues": {
    "safe_flags": {
      "--app": "string",
      "--assignee": "string",
      "--author": "string",
      "--closed": "string",
      "--commenter": "string",
      "--comments": "string",
      "--created": "string",
      "--include-prs": "none",
      "--interactions": "string",
      "--involves": "string",
      "--json": "string",
      "--label": "string",
      "--language": "string",
      "--limit": "number",
      "-L": "number",
      "--locked": "none",
      "--match": "string",
      "--mentions": "string",
      "--milestone": "string",
      "--no-assignee": "none",
      "--no-label": "none",
      "--no-milestone": "none",
      "--no-project": "none",
      "--order": "string",
      "--owner": "string",
      "--project": "string",
      "--reactions": "string",
      "--repo": "string",
      "-R": "string",
      "--sort": "string",
      "--state": "string",
      "--team-mentions": "string",
      "--updated": "string",
      "--visibility": "string",
    },
  },
  "gh search prs": {
    "safe_flags": {
      "--app": "string",
      "--assignee": "string",
      "--author": "string",
      "--base": "string",
      "-B": "string",
      "--checks": "string",
      "--closed": "string",
      "--commenter": "string",
      "--comments": "string",
      "--created": "string",
      "--draft": "none",
      "--head": "string",
      "-H": "string",
      "--interactions": "string",
      "--involves": "string",
      "--json": "string",
      "--label": "string",
      "--language": "string",
      "--limit": "number",
      "-L": "number",
      "--locked": "none",
      "--match": "string",
      "--mentions": "string",
      "--merged": "none",
      "--merged-at": "string",
      "--milestone": "string",
      "--no-assignee": "none",
      "--no-label": "none",
      "--no-milestone": "none",
      "--no-project": "none",
      "--order": "string",
      "--owner": "string",
      "--project": "string",
      "--reactions": "string",
      "--repo": "string",
      "-R": "string",
      "--review": "string",
      "--review-requested": "string",
      "--reviewed-by": "string",
      "--sort": "string",
      "--state": "string",
      "--team-mentions": "string",
      "--updated": "string",
      "--visibility": "string",
    },
  },
  "gh search commits": {
    "safe_flags": {
      "--author": "string",
      "--author-date": "string",
      "--author-email": "string",
      "--author-name": "string",
      "--committer": "string",
      "--committer-date": "string",
      "--committer-email": "string",
      "--committer-name": "string",
      "--hash": "string",
      "--json": "string",
      "--limit": "number",
      "-L": "number",
      "--merge": "none",
      "--order": "string",
      "--owner": "string",
      "--parent": "string",
      "--repo": "string",
      "-R": "string",
      "--sort": "string",
      "--tree": "string",
      "--visibility": "string",
    },
  },
  "gh search code": {
    "safe_flags": {
      "--extension": "string",
      "--filename": "string",
      "--json": "string",
      "--language": "string",
      "--limit": "number",
      "-L": "number",
      "--match": "string",
      "--owner": "string",
      "--repo": "string",
      "-R": "string",
      "--size": "string",
    },
  },
}


DOCKER_READ_ONLY_COMMANDS: dict[str, Any] = {
    "docker logs": {
      "safe_flags": {
        "--follow": "none",
        "-f": "none",
        "--tail": "string",
        "-n": "string",
        "--timestamps": "none",
        "-t": "none",
        "--since": "string",
        "--until": "string",
        "--details": "none",
      },
    },
    "docker inspect": {
      "safe_flags": {
        "--format": "string",
        "-f": "string",
        "--type": "string",
        "--size": "none",
        "-s": "none",
      },
    },
}


RIPGREP_READ_ONLY_COMMANDS: dict[str, Any] = {
    "rg": {
      "safe_flags": {
        "-e": "string",
        "--regexp": "string",
        "-f": "string",
        "-i": "none",
        "--ignore-case": "none",
        "-S": "none",
        "--smart-case": "none",
        "-F": "none",
        "--fixed-strings": "none",
        "-w": "none",
        "--word-regexp": "none",
        "-v": "none",
        "--invert-match": "none",
        "-c": "none",
        "--count": "none",
        "-l": "none",
        "--files-with-matches": "none",
        "--files-without-match": "none",
        "-n": "none",
        "--line-number": "none",
        "-o": "none",
        "--only-matching": "none",
        "-A": "number",
        "--after-context": "number",
        "-B": "number",
        "--before-context": "number",
        "-C": "number",
        "--context": "number",
        "-H": "none",
        "-h": "none",
        "--heading": "none",
        "--no-heading": "none",
        "-q": "none",
        "--quiet": "none",
        "--column": "none",
        "-g": "string",
        "--glob": "string",
        "-t": "string",
        "--type": "string",
        "-T": "string",
        "--type-not": "string",
        "--type-list": "none",
        "--hidden": "none",
        "--no-ignore": "none",
        "-u": "none",
        "-m": "number",
        "--max-count": "number",
        "-d": "number",
        "--max-depth": "number",
        "-a": "none",
        "--text": "none",
        "-z": "none",
        "-L": "none",
        "--follow": "none",
        "--color": "string",
        "--json": "none",
        "--stats": "none",
        "--help": "none",
        "--version": "none",
        "--debug": "none",
        "--": "none",
      },
    },
}


PYRIGHT_READ_ONLY_COMMANDS: dict[str, Any] = {
    "pyright": {
      "respects_double_dash": False,
      "safe_flags": {
        "--outputjson": "none",
        "--project": "string",
        "-p": "string",
        "--pythonversion": "string",
        "--pythonplatform": "string",
        "--typeshedpath": "string",
        "--venvpath": "string",
        "--level": "string",
        "--stats": "none",
        "--verbose": "none",
        "--version": "none",
        "--dependencies": "none",
        "--warnings": "none",
      },
      "callback": CALLBACKS['pyright'],
    },
}


FD_SAFE_FLAGS: dict[str, Any] = {
  "-h": "none",
  "--help": "none",
  "-V": "none",
  "--version": "none",
  "-H": "none",
  "--hidden": "none",
  "-I": "none",
  "--no-ignore": "none",
  "--no-ignore-vcs": "none",
  "--no-ignore-parent": "none",
  "-s": "none",
  "--case-sensitive": "none",
  "-i": "none",
  "--ignore-case": "none",
  "-g": "none",
  "--glob": "none",
  "--regex": "none",
  "-F": "none",
  "--fixed-strings": "none",
  "-a": "none",
  "--absolute-path": "none",
  "-L": "none",
  "--follow": "none",
  "-p": "none",
  "--full-path": "none",
  "-0": "none",
  "--print0": "none",
  "-d": "number",
  "--max-depth": "number",
  "--min-depth": "number",
  "--exact-depth": "number",
  "-t": "string",
  "--type": "string",
  "-e": "string",
  "--extension": "string",
  "-S": "string",
  "--size": "string",
  "--changed-within": "string",
  "--changed-before": "string",
  "-o": "string",
  "--owner": "string",
  "-E": "string",
  "--exclude": "string",
  "--ignore-file": "string",
  "-c": "string",
  "--color": "string",
  "-j": "number",
  "--threads": "number",
  "--max-buffer-time": "string",
  "--max-results": "number",
  "-1": "none",
  "-q": "none",
  "--quiet": "none",
  "--show-errors": "none",
  "--strip-cwd-prefix": "none",
  "--one-file-system": "none",
  "--prune": "none",
  "--search-path": "string",
  "--base-directory": "string",
  "--path-separator": "string",
  "--batch-size": "number",
  "--no-require-git": "none",
  "--hyperlink": "string",
  "--and": "string",
  "--format": "string",
}


_COMMAND_ALLOWLIST: dict[str, Any] = {
  "xargs": {
    "safe_flags": {
      "-I": "{}",
      "-n": "number",
      "-P": "number",
      "-L": "number",
      "-s": "number",
      "-E": "EOF",
      "-0": "none",
      "-t": "none",
      "-r": "none",
      "-x": "none",
      "-d": "char",
    },
  },
  **GIT_READ_ONLY_COMMANDS,
  "file": {
    "safe_flags": {
      "--brief": "none",
      "-b": "none",
      "--mime": "none",
      "-i": "none",
      "--mime-type": "none",
      "--mime-encoding": "none",
      "--apple": "none",
      "--check-encoding": "none",
      "-c": "none",
      "--exclude": "string",
      "--exclude-quiet": "string",
      "--print0": "none",
      "-0": "none",
      "-f": "string",
      "-F": "string",
      "--separator": "string",
      "--help": "none",
      "--version": "none",
      "-v": "none",
      "--no-dereference": "none",
      "-h": "none",
      "--dereference": "none",
      "-L": "none",
      "--magic-file": "string",
      "-m": "string",
      "--keep-going": "none",
      "-k": "none",
      "--list": "none",
      "-l": "none",
      "--no-buffer": "none",
      "-n": "none",
      "--preserve-date": "none",
      "-p": "none",
      "--raw": "none",
      "-r": "none",
      "-s": "none",
      "--special-files": "none",
      "--uncompress": "none",
      "-z": "none",
    },
  },
  "sed": {
    "safe_flags": {
      "--expression": "string",
      "-e": "string",
      "--quiet": "none",
      "--silent": "none",
      "-n": "none",
      "--regexp-extended": "none",
      "-r": "none",
      "--posix": "none",
      "-E": "none",
      "--line-length": "number",
      "-l": "number",
      "--zero-terminated": "none",
      "-z": "none",
      "--separate": "none",
      "-s": "none",
      "--unbuffered": "none",
      "-u": "none",
      "--debug": "none",
      "--help": "none",
      "--version": "none",
    },
    "callback": CALLBACKS['sed'],
  },
  "sort": {
    "safe_flags": {
      "--ignore-leading-blanks": "none",
      "-b": "none",
      "--dictionary-order": "none",
      "-d": "none",
      "--ignore-case": "none",
      "-f": "none",
      "--general-numeric-sort": "none",
      "-g": "none",
      "--human-numeric-sort": "none",
      "-h": "none",
      "--ignore-nonprinting": "none",
      "-i": "none",
      "--month-sort": "none",
      "-M": "none",
      "--numeric-sort": "none",
      "-n": "none",
      "--random-sort": "none",
      "-R": "none",
      "--reverse": "none",
      "-r": "none",
      "--sort": "string",
      "--stable": "none",
      "-s": "none",
      "--unique": "none",
      "-u": "none",
      "--version-sort": "none",
      "-V": "none",
      "--zero-terminated": "none",
      "-z": "none",
      "--key": "string",
      "-k": "string",
      "--field-separator": "string",
      "-t": "string",
      "--check": "none",
      "-c": "none",
      "--check-char-order": "none",
      "-C": "none",
      "--merge": "none",
      "-m": "none",
      "--buffer-size": "string",
      "-S": "string",
      "--parallel": "number",
      "--batch-size": "number",
      "--help": "none",
      "--version": "none",
    },
  },
  "man": {
    "safe_flags": {
      "-a": "none",
      "--all": "none",
      "-d": "none",
      "-f": "none",
      "--whatis": "none",
      "-h": "none",
      "-k": "none",
      "--apropos": "none",
      "-l": "string",
      "-w": "none",
      "-S": "string",
      "-s": "string",
    },
  },
  "help": {
    "safe_flags": {
      "-d": "none",
      "-m": "none",
      "-s": "none",
    },
  },
  "netstat": {
    "safe_flags": {
      "-a": "none",
      "-L": "none",
      "-l": "none",
      "-n": "none",
      "-f": "string",
      "-g": "none",
      "-i": "none",
      "-I": "string",
      "-s": "none",
      "-r": "none",
      "-m": "none",
      "-v": "none",
    },
  },
  "ps": {
    "safe_flags": {
      "-e": "none",
      "-A": "none",
      "-a": "none",
      "-d": "none",
      "-N": "none",
      "--deselect": "none",
      "-f": "none",
      "-F": "none",
      "-l": "none",
      "-j": "none",
      "-y": "none",
      "-w": "none",
      "-ww": "none",
      "--width": "number",
      "-c": "none",
      "-H": "none",
      "--forest": "none",
      "--headers": "none",
      "--no-headers": "none",
      "-n": "string",
      "--sort": "string",
      "-L": "none",
      "-T": "none",
      "-m": "none",
      "-C": "string",
      "-G": "string",
      "-g": "string",
      "-p": "string",
      "--pid": "string",
      "-q": "string",
      "--quick-pid": "string",
      "-s": "string",
      "--sid": "string",
      "-t": "string",
      "--tty": "string",
      "-U": "string",
      "-u": "string",
      "--user": "string",
      "--help": "none",
      "--info": "none",
      "-V": "none",
      "--version": "none",
    },
    "callback": CALLBACKS['ps'],
  },
  "base64": {
    "respects_double_dash": False,
    "safe_flags": {
      "-d": "none",
      "-D": "none",
      "--decode": "none",
      "-b": "number",
      "--break": "number",
      "-w": "number",
      "--wrap": "number",
      "-i": "string",
      "--input": "string",
      "--ignore-garbage": "none",
      "-h": "none",
      "--help": "none",
      "--version": "none",
    },
  },
  "grep": {
    "safe_flags": {
      "-e": "string",
      "--regexp": "string",
      "-f": "string",
      "--file": "string",
      "-F": "none",
      "--fixed-strings": "none",
      "-G": "none",
      "--basic-regexp": "none",
      "-E": "none",
      "--extended-regexp": "none",
      "-P": "none",
      "--perl-regexp": "none",
      "-i": "none",
      "--ignore-case": "none",
      "--no-ignore-case": "none",
      "-v": "none",
      "--invert-match": "none",
      "-w": "none",
      "--word-regexp": "none",
      "-x": "none",
      "--line-regexp": "none",
      "-c": "none",
      "--count": "none",
      "--color": "string",
      "--colour": "string",
      "-L": "none",
      "--files-without-match": "none",
      "-l": "none",
      "--files-with-matches": "none",
      "-m": "number",
      "--max-count": "number",
      "-o": "none",
      "--only-matching": "none",
      "-q": "none",
      "--quiet": "none",
      "--silent": "none",
      "-s": "none",
      "--no-messages": "none",
      "-b": "none",
      "--byte-offset": "none",
      "-H": "none",
      "--with-filename": "none",
      "-h": "none",
      "--no-filename": "none",
      "--label": "string",
      "-n": "none",
      "--line-number": "none",
      "-T": "none",
      "--initial-tab": "none",
      "-u": "none",
      "--unix-byte-offsets": "none",
      "-Z": "none",
      "--null": "none",
      "-z": "none",
      "--null-data": "none",
      "-A": "number",
      "--after-context": "number",
      "-B": "number",
      "--before-context": "number",
      "-C": "number",
      "--context": "number",
      "--group-separator": "string",
      "--no-group-separator": "none",
      "-a": "none",
      "--text": "none",
      "--binary-files": "string",
      "-D": "string",
      "--devices": "string",
      "-d": "string",
      "--directories": "string",
      "--exclude": "string",
      "--exclude-from": "string",
      "--exclude-dir": "string",
      "--include": "string",
      "-r": "none",
      "--recursive": "none",
      "-R": "none",
      "--dereference-recursive": "none",
      "--line-buffered": "none",
      "-U": "none",
      "--binary": "none",
      "--help": "none",
      "-V": "none",
      "--version": "none",
    },
  },
  **RIPGREP_READ_ONLY_COMMANDS,
  "sha256sum": {
    "safe_flags": {
      "-b": "none",
      "--binary": "none",
      "-t": "none",
      "--text": "none",
      "-c": "none",
      "--check": "none",
      "--ignore-missing": "none",
      "--quiet": "none",
      "--status": "none",
      "--strict": "none",
      "-w": "none",
      "--warn": "none",
      "--tag": "none",
      "-z": "none",
      "--zero": "none",
      "--help": "none",
      "--version": "none",
    },
  },
  "sha1sum": {
    "safe_flags": {
      "-b": "none",
      "--binary": "none",
      "-t": "none",
      "--text": "none",
      "-c": "none",
      "--check": "none",
      "--ignore-missing": "none",
      "--quiet": "none",
      "--status": "none",
      "--strict": "none",
      "-w": "none",
      "--warn": "none",
      "--tag": "none",
      "-z": "none",
      "--zero": "none",
      "--help": "none",
      "--version": "none",
    },
  },
  "md5sum": {
    "safe_flags": {
      "-b": "none",
      "--binary": "none",
      "-t": "none",
      "--text": "none",
      "-c": "none",
      "--check": "none",
      "--ignore-missing": "none",
      "--quiet": "none",
      "--status": "none",
      "--strict": "none",
      "-w": "none",
      "--warn": "none",
      "--tag": "none",
      "-z": "none",
      "--zero": "none",
      "--help": "none",
      "--version": "none",
    },
  },
  "tree": {
    "safe_flags": {
      "-a": "none",
      "-d": "none",
      "-l": "none",
      "-f": "none",
      "-x": "none",
      "-L": "number",
      "-P": "string",
      "-I": "string",
      "--gitignore": "none",
      "--gitfile": "string",
      "--ignore-case": "none",
      "--matchdirs": "none",
      "--metafirst": "none",
      "--prune": "none",
      "--info": "none",
      "--infofile": "string",
      "--noreport": "none",
      "--charset": "string",
      "--filelimit": "number",
      "-q": "none",
      "-N": "none",
      "-Q": "none",
      "-p": "none",
      "-u": "none",
      "-g": "none",
      "-s": "none",
      "-h": "none",
      "--si": "none",
      "--du": "none",
      "-D": "none",
      "--timefmt": "string",
      "-F": "none",
      "--inodes": "none",
      "--device": "none",
      "-v": "none",
      "-t": "none",
      "-c": "none",
      "-U": "none",
      "-r": "none",
      "--dirsfirst": "none",
      "--filesfirst": "none",
      "--sort": "string",
      "-i": "none",
      "-A": "none",
      "-S": "none",
      "-n": "none",
      "-C": "none",
      "-X": "none",
      "-J": "none",
      "-H": "string",
      "--nolinks": "none",
      "--hintro": "string",
      "--houtro": "string",
      "-T": "string",
      "--hyperlink": "none",
      "--scheme": "string",
      "--authority": "string",
      "--fromfile": "none",
      "--fromtabfile": "none",
      "--fflinks": "none",
      "--help": "none",
      "--version": "none",
    },
  },
  "date": {
    "safe_flags": {
      "-d": "string",
      "--date": "string",
      "-r": "string",
      "--reference": "string",
      "-u": "none",
      "--utc": "none",
      "--universal": "none",
      "-I": "none",
      "--iso-8601": "string",
      "-R": "none",
      "--rfc-email": "none",
      "--rfc-3339": "string",
      "--debug": "none",
      "--help": "none",
      "--version": "none",
    },
    "callback": CALLBACKS['date'],
  },
  "hostname": {
    "safe_flags": {
      "-f": "none",
      "--fqdn": "none",
      "--long": "none",
      "-s": "none",
      "--short": "none",
      "-i": "none",
      "--ip-address": "none",
      "-I": "none",
      "--all-ip-addresses": "none",
      "-a": "none",
      "--alias": "none",
      "-d": "none",
      "--domain": "none",
      "-A": "none",
      "--all-fqdns": "none",
      "-v": "none",
      "--verbose": "none",
      "-h": "none",
      "--help": "none",
      "-V": "none",
      "--version": "none",
    },
    "regex": REGEXES['hostname'],
  },
  "info": {
    "safe_flags": {
      "-f": "string",
      "--file": "string",
      "-d": "string",
      "--directory": "string",
      "-n": "string",
      "--node": "string",
      "-a": "none",
      "--all": "none",
      "-k": "string",
      "--apropos": "string",
      "-w": "none",
      "--where": "none",
      "--location": "none",
      "--show-options": "none",
      "--vi-keys": "none",
      "--subnodes": "none",
      "-h": "none",
      "--help": "none",
      "--usage": "none",
      "--version": "none",
    },
  },
  "lsof": {
    "safe_flags": {
      "-?": "none",
      "-h": "none",
      "-v": "none",
      "-a": "none",
      "-b": "none",
      "-C": "none",
      "-l": "none",
      "-n": "none",
      "-N": "none",
      "-O": "none",
      "-P": "none",
      "-Q": "none",
      "-R": "none",
      "-t": "none",
      "-U": "none",
      "-V": "none",
      "-X": "none",
      "-H": "none",
      "-E": "none",
      "-F": "none",
      "-g": "none",
      "-i": "none",
      "-K": "none",
      "-L": "none",
      "-o": "none",
      "-r": "none",
      "-s": "none",
      "-S": "none",
      "-T": "none",
      "-x": "none",
      "-A": "string",
      "-c": "string",
      "-d": "string",
      "-e": "string",
      "-k": "string",
      "-p": "string",
      "-u": "string",
    },
    "callback": CALLBACKS['lsof'],
  },
  "pgrep": {
    "safe_flags": {
      "-d": "string",
      "--delimiter": "string",
      "-l": "none",
      "--list-name": "none",
      "-a": "none",
      "--list-full": "none",
      "-v": "none",
      "--inverse": "none",
      "-w": "none",
      "--lightweight": "none",
      "-c": "none",
      "--count": "none",
      "-f": "none",
      "--full": "none",
      "-g": "string",
      "--pgroup": "string",
      "-G": "string",
      "--group": "string",
      "-i": "none",
      "--ignore-case": "none",
      "-n": "none",
      "--newest": "none",
      "-o": "none",
      "--oldest": "none",
      "-O": "string",
      "--older": "string",
      "-P": "string",
      "--parent": "string",
      "-s": "string",
      "--session": "string",
      "-t": "string",
      "--terminal": "string",
      "-u": "string",
      "--euid": "string",
      "-U": "string",
      "--uid": "string",
      "-x": "none",
      "--exact": "none",
      "-F": "string",
      "--pidfile": "string",
      "-L": "none",
      "--logpidfile": "none",
      "-r": "string",
      "--runstates": "string",
      "--ns": "string",
      "--nslist": "string",
      "--help": "none",
      "-V": "none",
      "--version": "none",
    },
  },
  "tput": {
    "safe_flags": {
      "-T": "string",
      "-V": "none",
      "-x": "none",
    },
    "callback": CALLBACKS['tput'],
  },
  "ss": {
    "safe_flags": {
      "-h": "none",
      "--help": "none",
      "-V": "none",
      "--version": "none",
      "-n": "none",
      "--numeric": "none",
      "-r": "none",
      "--resolve": "none",
      "-a": "none",
      "--all": "none",
      "-l": "none",
      "--listening": "none",
      "-o": "none",
      "--options": "none",
      "-e": "none",
      "--extended": "none",
      "-m": "none",
      "--memory": "none",
      "-p": "none",
      "--processes": "none",
      "-i": "none",
      "--info": "none",
      "-s": "none",
      "--summary": "none",
      "-4": "none",
      "--ipv4": "none",
      "-6": "none",
      "--ipv6": "none",
      "-0": "none",
      "--packet": "none",
      "-t": "none",
      "--tcp": "none",
      "-M": "none",
      "--mptcp": "none",
      "-S": "none",
      "--sctp": "none",
      "-u": "none",
      "--udp": "none",
      "-d": "none",
      "--dccp": "none",
      "-w": "none",
      "--raw": "none",
      "-x": "none",
      "--unix": "none",
      "--tipc": "none",
      "--vsock": "none",
      "-f": "string",
      "--family": "string",
      "-A": "string",
      "--query": "string",
      "--socket": "string",
      "-Z": "none",
      "--context": "none",
      "-z": "none",
      "--contexts": "none",
      "-b": "none",
      "--bpf": "none",
      "-E": "none",
      "--events": "none",
      "-H": "none",
      "--no-header": "none",
      "-O": "none",
      "--oneline": "none",
      "--tipcinfo": "none",
      "--tos": "none",
      "--cgroup": "none",
      "--inet-sockopt": "none",
    },
  },
  "fd": {"safe_flags": {**FD_SAFE_FLAGS}},
  "fdfind": {"safe_flags": {**FD_SAFE_FLAGS}},
  **PYRIGHT_READ_ONLY_COMMANDS,
  **DOCKER_READ_ONLY_COMMANDS,
}


_ANT_ONLY_COMMAND_ALLOWLIST: dict[str, Any] = {
  **GH_READ_ONLY_COMMANDS,
  "aki": {
    "safe_flags": {
      "-h": "none",
      "--help": "none",
      "-k": "none",
      "--keyword": "none",
      "-s": "none",
      "--semantic": "none",
      "--no-adaptive": "none",
      "-n": "number",
      "--limit": "number",
      "-o": "number",
      "--offset": "number",
      "--source": "string",
      "--exclude-source": "string",
      "-a": "string",
      "--after": "string",
      "-b": "string",
      "--before": "string",
      "--collection": "string",
      "--drive": "string",
      "--folder": "string",
      "--descendants": "none",
      "-m": "string",
      "--meta": "string",
      "-t": "string",
      "--threshold": "string",
      "--kw-weight": "string",
      "--sem-weight": "string",
      "-j": "none",
      "--json": "none",
      "-c": "none",
      "--chunk": "none",
      "--preview": "none",
      "-d": "none",
      "--full-doc": "none",
      "-v": "none",
      "--verbose": "none",
      "--stats": "none",
      "-S": "number",
      "--summarize": "number",
      "--explain": "none",
      "--examine": "string",
      "--url": "string",
      "--multi-turn": "number",
      "--multi-turn-model": "string",
      "--multi-turn-context": "string",
      "--no-rerank": "none",
      "--audit": "none",
      "--local": "none",
      "--staging": "none",
    },
  },
}


# ── assembled configs ────────────────────────────────────────────────────────

def _configs(raw: Mapping[str, Any]) -> dict[str, CommandConfig]:
    return {
        name: CommandConfig(
            safe_flags=dict(entry["safe_flags"]),
            regex=entry.get("regex"),
            callback=entry.get("callback"),
            respects_double_dash=entry.get("respects_double_dash", True),
        )
        for name, entry in raw.items()
    }


COMMAND_ALLOWLIST: dict[str, CommandConfig] = _configs(_COMMAND_ALLOWLIST)
ANT_ONLY_COMMAND_ALLOWLIST: dict[str, CommandConfig] = _configs(_ANT_ONLY_COMMAND_ALLOWLIST)

#: `EXTERNAL_READONLY_COMMANDS`.
EXTERNAL_READONLY_COMMANDS: tuple[str, ...] = ("docker ps", "docker images")

#: `SAFE_TARGET_COMMANDS_FOR_XARGS`: no flag of these can write, execute or reach the network.
SAFE_TARGET_COMMANDS_FOR_XARGS: tuple[str, ...] = ("echo", "printf", "wc", "grep", "head", "tail")


def get_command_allowlist(env: Mapping[str, str] | None = None) -> dict[str, CommandConfig]:
    """`getCommandAllowlist`: no xargs on Windows; the ant-only entries for `USER_TYPE=ant`."""
    import os

    source = os.environ if env is None else env
    allowlist = dict(COMMAND_ALLOWLIST)
    if get_platform() == "windows":
        allowlist.pop("xargs", None)
    if source.get("USER_TYPE") == "ant":
        return {**allowlist, **ANT_ONLY_COMMAND_ALLOWLIST}
    return allowlist


# ── UNC paths ────────────────────────────────────────────────────────────────

_UNC_BACKSLASH = re.compile(r"\\\\[^" + JS_WS + r"\\/]+(?:@(?:\d+|ssl))?(?:[\\/]|\Z|" + _S + ")", re.IGNORECASE)
_UNC_FORWARD = re.compile(r"(?<!:)//[^" + JS_WS + r"\\/]+(?:@(?:\d+|ssl))?(?:[\\/]|\Z|" + _S + ")", re.IGNORECASE)
_UNC_MIXED = re.compile(r"/\\{2,}[^" + JS_WS + r"\\/]")
_UNC_REVERSE_MIXED = re.compile(r"\\{2,}/[^" + JS_WS + r"\\/]")


def contains_vulnerable_unc_path(path_or_command: str) -> bool:
    """`containsVulnerableUncPath`: Windows only; UNC/WebDAV forms that leak credentials."""
    if get_platform() != "windows":
        return False
    s = path_or_command
    if _UNC_BACKSLASH.search(s) or _UNC_FORWARD.search(s) or _UNC_MIXED.search(s) or _UNC_REVERSE_MIXED.search(s):
        return True
    if re.search(r"@SSL@\d+", s, re.IGNORECASE) or re.search(r"@\d+@SSL", s, re.IGNORECASE):
        return True
    if re.search("DavWWWRoot", s, re.IGNORECASE):
        return True
    if re.match(r"\\\\(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[\\/]", s) or re.match(
        r"//(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[\\/]", s
    ):
        return True
    return bool(re.match(r"\\\\(\[[\da-fA-F:]+\])[\\/]", s) or re.match(r"//(\[[\da-fA-F:]+\])[\\/]", s))


# ── flag validation ──────────────────────────────────────────────────────────

FLAG_PATTERN = re.compile(r"-[a-zA-Z0-9_-]")


def validate_flag_argument(value: str, arg_type: str) -> bool:
    """`validateFlagArgument`."""
    if arg_type == "number":
        return re.fullmatch(r"[0-9]+", value) is not None
    if arg_type == "string":
        return True
    if arg_type == "char":
        return len(value) == 1
    if arg_type == "{}":
        return value == "{}"
    if arg_type == "EOF":
        return value == "EOF"
    return False


def _is_flag_token(token: str) -> bool:
    return token.startswith("-") and len(token) > 1 and FLAG_PATTERN.match(token) is not None


def validate_flags(
    tokens: list[str],
    start_index: int,
    config: CommandConfig,
    *,
    command_name: str | None = None,
    xargs_target_commands: tuple[str, ...] | None = None,
) -> bool:
    """`validateFlags`: every flag after the command words must be a known safe flag."""
    i = start_index
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue

        if xargs_target_commands is not None and command_name == "xargs" and (
            not token.startswith("-") or token == "--"
        ):
            if token == "--" and i + 1 < len(tokens):
                i += 1
                token = tokens[i]
            return bool(token) and token in xargs_target_commands

        if token == "--":
            i += 1
            if config.respects_double_dash:
                break
            continue

        if not _is_flag_token(token):
            i += 1
            continue

        has_equals = "=" in token
        flag, _, inline_value = token.partition("=")
        if not flag:
            return False
        arg_type = config.safe_flags.get(flag)

        if not arg_type:
            if command_name == "git" and re.fullmatch(r"-[0-9]+", flag):
                i += 1
                continue
            if command_name in ("grep", "rg") and not flag.startswith("--") and len(flag) > 2:
                potential_flag, potential_value = flag[:2], flag[2:]
                attached_type = config.safe_flags.get(potential_flag)
                if attached_type and re.fullmatch(r"[0-9]+", potential_value):
                    if attached_type in ("number", "string"):
                        if validate_flag_argument(potential_value, attached_type):
                            i += 1
                            continue
                        return False  # pragma: no cover - a digit run is always a valid number/string
            if not flag.startswith("--") and len(flag) > 2:
                for ch in flag[1:]:
                    bundled = config.safe_flags.get("-" + ch)
                    if not bundled or bundled != "none":
                        return False
                i += 1
                continue
            return False

        if arg_type == "none":
            if has_equals:
                return False
            i += 1
            continue

        if has_equals:
            arg_value = inline_value
            i += 1
        else:
            if i + 1 >= len(tokens) or (tokens[i + 1] and _is_flag_token(tokens[i + 1])):
                return False
            arg_value = tokens[i + 1] or ""
            i += 2

        if arg_type == "string" and arg_value.startswith("-"):
            reverse_sort = flag == "--sort" and command_name == "git" and re.match(r"-[a-zA-Z]", arg_value)
            if not reverse_sort:
                return False
        if not validate_flag_argument(arg_value, arg_type):
            return False
    return True


__all__ = [
    "ANT_ONLY_COMMAND_ALLOWLIST",
    "COMMAND_ALLOWLIST",
    "CommandConfig",
    "EXTERNAL_READONLY_COMMANDS",
    "FLAG_PATTERN",
    "SAFE_TARGET_COMMANDS_FOR_XARGS",
    "contains_vulnerable_unc_path",
    "get_command_allowlist",
    "gh_is_dangerous_callback",
    "validate_flag_argument",
    "validate_flags",
]
