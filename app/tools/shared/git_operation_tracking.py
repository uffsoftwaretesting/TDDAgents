"""
Git operations in a shell command and its output, for the collapsed tool-use summary.

Ported from `reference/claude-code/src/tools/shared/gitOperationTracking.ts` -> `gitCmdRe`,
`GH_PR_ACTIONS`, `parsePrUrl`, `findPrInStdout`, `parseGitCommitId`, `parseGitPushBranch`,
`parsePrNumberFromText`, `parseRefFromCommand`, `detectGitOperation`. Not ported:
`trackGitOperations`, whose only job is upstream's OTLP counters and analytics events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

CommitKind = Literal["committed", "amended", "cherry-picked"]
BranchAction = Literal["merged", "rebased"]
PrAction = Literal["created", "edited", "merged", "commented", "closed", "ready"]


def git_cmd_re(subcmd: str, suffix: str = "") -> re.Pattern[str]:
    """`gitCmdRe`: `git <subcmd>`, tolerating global options (`-c k=v`, `-C path`, `--x=y`)."""
    return re.compile(rf"\bgit(?:\s+-[cC]\s+\S+|\s+--\S+=\S+)*\s+{subcmd}\b{suffix}")


GIT_COMMIT_RE = git_cmd_re("commit")
GIT_PUSH_RE = git_cmd_re("push")
GIT_CHERRY_PICK_RE = git_cmd_re("cherry-pick")
GIT_MERGE_RE = git_cmd_re("merge", "(?!-)")
GIT_REBASE_RE = git_cmd_re("rebase")

#: `GH_PR_ACTIONS`.
GH_PR_ACTIONS: tuple[tuple[re.Pattern[str], PrAction], ...] = (
    (re.compile(r"\bgh\s+pr\s+create\b"), "created"),
    (re.compile(r"\bgh\s+pr\s+edit\b"), "edited"),
    (re.compile(r"\bgh\s+pr\s+merge\b"), "merged"),
    (re.compile(r"\bgh\s+pr\s+comment\b"), "commented"),
    (re.compile(r"\bgh\s+pr\s+close\b"), "closed"),
    (re.compile(r"\bgh\s+pr\s+ready\b"), "ready"),
)


@dataclass(frozen=True, slots=True)
class Commit:
    sha: str
    kind: CommitKind


@dataclass(frozen=True, slots=True)
class Push:
    branch: str


@dataclass(frozen=True, slots=True)
class BranchOp:
    ref: str
    action: BranchAction


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    action: PrAction
    url: str | None = None


@dataclass(frozen=True, slots=True)
class GitOperation:
    """`detectGitOperation`'s result: each part present when the command did it."""

    commit: Commit | None = None
    push: Push | None = None
    branch: BranchOp | None = None
    pr: PullRequest | None = None


def parse_pr_url(url: str) -> tuple[int, str, str] | None:
    """`parsePrUrl`: (number, url, `owner/repo`) of a GitHub PR URL."""
    match = re.search(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
    if match is None:
        return None
    return int(match.group(2)), url, match.group(1)


def find_pr_in_stdout(stdout: str) -> tuple[int, str, str] | None:
    """`findPrInStdout`."""
    match = re.search(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", stdout)
    return parse_pr_url(match.group(0)) if match else None


def parse_git_commit_id(stdout: str) -> str | None:
    """`parseGitCommitId`: the sha of `[branch abc1234] message` (or `(root-commit)`)."""
    match = re.search(r"\[[\w./-]+(?: \(root-commit\))? ([0-9a-f]+)\]", stdout)
    return match.group(1) if match else None


def parse_git_push_branch(output: str) -> str | None:
    """`parseGitPushBranch`: the target of a push's ref-update line."""
    match = re.search(r"^\s*[+\-*!= ]?\s*(?:\[new branch\]|\S+\.\.+\S+)\s+\S+\s*->\s*(\S+)", output, re.MULTILINE)
    return match.group(1) if match else None


def parse_pr_number_from_text(stdout: str) -> int | None:
    """`parsePrNumberFromText`: `Pull request owner/repo#1234`."""
    match = re.search(r"[Pp]ull request (?:\S+#)?#?(\d+)", stdout)
    return int(match.group(1)) if match else None


def parse_ref_from_command(command: str, verb: str) -> str | None:
    """`parseRefFromCommand`: the first non-flag argument after `git <verb>`."""
    parts = git_cmd_re(verb).split(command)  # `split(re)[1]`: up to the next match, as upstream
    if len(parts) < 2 or not parts[1]:
        return None
    for token in parts[1].strip().split():
        if re.match(r"^[&|;><]", token):
            break
        if token.startswith("-"):
            continue
        return token
    return None


def detect_git_operation(command: str, output: str) -> GitOperation:
    """`detectGitOperation`: what a command did, judged by the command and then its output."""
    commit = push = branch = pr = None
    is_cherry_pick = GIT_CHERRY_PICK_RE.search(command) is not None
    if GIT_COMMIT_RE.search(command) or is_cherry_pick:
        sha = parse_git_commit_id(output)
        if sha:
            kind: CommitKind = ("cherry-picked" if is_cherry_pick
                                else "amended" if re.search(r"--amend\b", command) else "committed")
            commit = Commit(sha=sha[:6], kind=kind)
    if GIT_PUSH_RE.search(command):
        pushed = parse_git_push_branch(output)
        if pushed:
            push = Push(branch=pushed)
    if GIT_MERGE_RE.search(command) and re.search(r"(Fast-forward|Merge made by)", output):
        ref = parse_ref_from_command(command, "merge")
        if ref:
            branch = BranchOp(ref=ref, action="merged")
    if GIT_REBASE_RE.search(command) and "Successfully rebased" in output:
        ref = parse_ref_from_command(command, "rebase")
        if ref:
            branch = BranchOp(ref=ref, action="rebased")
    action = next((a for pattern, a in GH_PR_ACTIONS if pattern.search(command)), None)
    if action is not None:
        found = find_pr_in_stdout(output)
        if found is not None:
            pr = PullRequest(number=found[0], action=action, url=found[1])
        else:
            number = parse_pr_number_from_text(output)
            if number:
                pr = PullRequest(number=number, action=action)
    return GitOperation(commit=commit, push=push, branch=branch, pr=pr)


__all__ = [
    "BranchOp",
    "Commit",
    "GitOperation",
    "PullRequest",
    "Push",
    "detect_git_operation",
    "git_cmd_re",
    "parse_git_commit_id",
    "parse_git_push_branch",
    "parse_pr_number_from_text",
    "parse_pr_url",
    "parse_ref_from_command",
]
