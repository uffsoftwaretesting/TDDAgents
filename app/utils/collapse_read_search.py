"""
Collapse consecutive search/read tool uses into one summary.

Ported from `reference/claude-code/src/utils/collapseReadSearch.ts` -> `SearchOrReadResult`,
`getToolSearchOrReadInfo`, `isMemorySearch`, `isMemoryWriteOrEdit`, `commandAsHint`,
`MAX_HINT_CHARS`, the group accumulator (`createEmptyGroup`, the branches of
`collapseReadSearchGroups`, `scanBashResultForGitOps`, `createCollapsedGroup`),
`getSearchReadSummaryText` and `summarizeRecentActivities`, with the summary line of
`components/messages/CollapsedReadSearchContent.tsx` (`collapsed_summary_text`).

Upstream groups a rendered message list and re-renders the row in place; TDDAgents' REPL prints
lines, so `ReadSearchGroup` is fed one call and one result at a time and the REPL prints the hint
of each call as it arrives and the past-tense summary when the group closes (user decision
2026-11-01). Not ported, because their subsystems do not exist here: the REPL tool and its
primitives, Snip and ToolSearch absorption, team memory (`TEAMMEM`), PreToolUse hook summaries
(this port emits no `stop_hook_summary` message) and the `(ctrl+o to expand)` hint (the REPL's
`--verbose` flag is the expanded view).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.tool import Tool, find_tool_by_name
from app.tools.bash_tool.comment_label import extract_bash_comment_label
from app.tools.shared.git_operation_tracking import (
    BranchOp,
    Commit,
    PullRequest,
    Push,
    detect_git_operation,
)
from app.utils.file import get_display_path
from app.utils.fullscreen import is_fullscreen_env_enabled
from app.utils.memory_file_detection import (
    is_auto_managed_memory_file,
    is_auto_managed_memory_pattern,
    is_memory_directory,
    is_shell_command_targeting_memory,
)

BASH_TOOL_NAME = "Bash"
FILE_EDIT_TOOL_NAME = "Edit"
FILE_WRITE_TOOL_NAME = "WriteFile"

#: `MAX_HINT_CHARS`: about five lines of sixty columns.
MAX_HINT_CHARS = 300


@dataclass(frozen=True, slots=True)
class SearchOrReadResult:
    """`SearchOrReadResult`."""

    is_collapsible: bool
    is_search: bool = False
    is_read: bool = False
    is_list: bool = False
    is_memory_write: bool = False
    is_absorbed_silently: bool = False
    mcp_server_name: str | None = None
    is_bash: bool | None = None


_NOT_COLLAPSIBLE = SearchOrReadResult(is_collapsible=False)


def command_as_hint(command: str) -> str:
    """`commandAsHint`: `$ cmd`, blank lines dropped, runs of whitespace collapsed, capped."""
    cleaned = "$ " + "\n".join(line for line in (re.sub(r"\s+", " ", raw).strip() for raw in command.split("\n"))
                               if line)
    return cleaned[: MAX_HINT_CHARS - 1] + "…" if len(cleaned) > MAX_HINT_CHARS else cleaned


def _file_path_of(tool_input: Mapping[str, Any]) -> str | None:
    """`getFilePathFromToolInput`: `file_path`, else `path`."""
    value = tool_input.get("file_path") or tool_input.get("path")
    return str(value) if value else None


def is_memory_search(context: Any, tool_input: Mapping[str, Any]) -> bool:
    """`isMemorySearch`: a search whose path, glob or shell command targets memory."""
    path = tool_input.get("path")
    if path and (is_auto_managed_memory_file(context, str(path)) or is_memory_directory(context, str(path))):
        return True
    glob = tool_input.get("glob")
    if glob and is_auto_managed_memory_pattern(context, str(glob)):
        return True
    command = tool_input.get("command")
    return bool(command) and is_shell_command_targeting_memory(context, str(command))


def is_memory_write_or_edit(context: Any, tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """`isMemoryWriteOrEdit`: a Write or Edit of an auto-managed memory file."""
    if tool_name not in (FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME):
        return False
    path = _file_path_of(tool_input)
    return path is not None and is_auto_managed_memory_file(context, path)


def get_tool_search_or_read_info(
    tool_name: str, tool_input: Mapping[str, Any], tools: Sequence[Tool], context: Any = None
) -> SearchOrReadResult:
    """`getToolSearchOrReadInfo`: whether, and as what, a tool use collapses."""
    if is_memory_write_or_edit(context, tool_name, tool_input):
        return SearchOrReadResult(is_collapsible=True, is_memory_write=True)
    tool = find_tool_by_name(tools, tool_name)
    result = tool.is_search_or_read_command(dict(tool_input)) if tool is not None else None
    if result is None:
        return _NOT_COLLAPSIBLE
    collapsible = result.is_search or result.is_read or result.is_list
    fullscreen = is_fullscreen_env_enabled()
    return SearchOrReadResult(
        is_collapsible=collapsible or (fullscreen and tool_name == BASH_TOOL_NAME),
        is_search=result.is_search,
        is_read=result.is_read,
        is_list=result.is_list,
        mcp_server_name=getattr(tool, "server_name", None) if getattr(tool, "is_mcp", False) else None,
        is_bash=(not collapsible and tool_name == BASH_TOOL_NAME) if fullscreen else None,
    )


@dataclass
class ReadSearchGroup:
    """`GroupAccumulator`: one run of collapsed tool uses and their results."""

    context: Any = None
    tool_use_ids: list[str] = field(default_factory=list)
    calls: list[Mapping[str, Any]] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    search_count: int = 0
    read_file_paths: list[str] = field(default_factory=list)
    read_operation_count: int = 0
    list_count: int = 0
    memory_search_count: int = 0
    memory_read_file_paths: list[str] = field(default_factory=list)
    memory_write_count: int = 0
    non_mem_search_args: list[str] = field(default_factory=list)
    #: The most recent non-memory operation, formatted for the `⎿` hint.
    latest_display_hint: str | None = None
    mcp_call_count: int = 0
    mcp_server_names: list[str] = field(default_factory=list)
    bash_count: int = 0
    bash_commands: dict[str, str] = field(default_factory=dict)
    commits: list[Commit] = field(default_factory=list)
    pushes: list[Push] = field(default_factory=list)
    branches: list[BranchOp] = field(default_factory=list)
    prs: list[PullRequest] = field(default_factory=list)
    git_op_bash_count: int = 0
    relevant_memories: list[Mapping[str, Any]] = field(default_factory=list)

    def owns(self, tool_use_id: str) -> bool:
        """`isCollapsibleToolResult` for one result: it answers a call in this group."""
        return tool_use_id in self.tool_use_ids

    def add_tool_use(self, call: Mapping[str, Any], info: SearchOrReadResult) -> None:
        """One collapsible tool use, counted as `collapseReadSearchGroups` counts it."""
        tool_input: Mapping[str, Any] = call.get("args") or {}
        command = tool_input.get("command")
        if info.is_memory_write:
            self.memory_write_count += 1
        elif info.is_absorbed_silently:
            pass
        elif info.mcp_server_name:
            self.mcp_call_count += 1
            if info.mcp_server_name not in self.mcp_server_names:
                self.mcp_server_names.append(info.mcp_server_name)
            if tool_input.get("query"):
                self.latest_display_hint = f'"{tool_input["query"]}"'
        elif is_fullscreen_env_enabled() and info.is_bash:
            self.bash_count += 1
            if command:
                self.latest_display_hint = extract_bash_comment_label(str(command)) or command_as_hint(str(command))
                self.bash_commands[str(call.get("id") or "")] = str(command)
        elif info.is_list:
            self.list_count += 1
            if command:
                self.latest_display_hint = command_as_hint(str(command))
        elif info.is_search:
            self.search_count += 1
            if is_memory_search(self.context, tool_input):
                self.memory_search_count += 1
            elif tool_input.get("pattern"):
                self.non_mem_search_args.append(str(tool_input["pattern"]))
                self.latest_display_hint = f'"{tool_input["pattern"]}"'
        else:
            file_path = tool_input.get("file_path")
            if file_path:
                path = str(file_path)
                if path not in self.read_file_paths:
                    self.read_file_paths.append(path)
                if is_auto_managed_memory_file(self.context, path):
                    if path not in self.memory_read_file_paths:
                        self.memory_read_file_paths.append(path)
                else:
                    self.latest_display_hint = get_display_path(path, self.context)
            else:
                self.read_operation_count += 1
                if command:
                    self.latest_display_hint = command_as_hint(str(command))
        self.tool_use_ids.append(str(call.get("id") or ""))
        self.calls.append(call)

    def absorb_result(self, tool_use_id: str, tool_use_result: Any) -> None:
        """A result of this group's calls (`scanBashResultForGitOps` under fullscreen)."""
        self.results[tool_use_id] = tool_use_result
        command = self.bash_commands.get(tool_use_id)
        if not (is_fullscreen_env_enabled() and command and isinstance(tool_use_result, Mapping)):
            return
        stdout, stderr = tool_use_result.get("stdout") or "", tool_use_result.get("stderr") or ""
        if not stdout and not stderr:
            return
        operation = detect_git_operation(command, f"{stdout}\n{stderr}")
        if operation.commit is not None:
            self.commits.append(operation.commit)
        if operation.push is not None:
            self.pushes.append(operation.push)
        if operation.branch is not None:
            self.branches.append(operation.branch)
        if operation.pr is not None:
            self.prs.append(operation.pr)
        if operation.commit or operation.push or operation.branch or operation.pr:
            self.git_op_bash_count += 1

    def absorb_relevant_memories(self, memories: Iterable[Mapping[str, Any]]) -> None:
        """A `relevant_memories` attachment inside the group: counted as recalled memories."""
        self.relevant_memories.extend(memories)

    @property
    def is_empty(self) -> bool:
        return not self.tool_use_ids

    # ── `createCollapsedGroup` ──────────────────────────────────────────────

    @property
    def read_count(self) -> int:
        """Unique file reads (operation count only without them), memory reads excluded."""
        total = len(self.read_file_paths) if self.read_file_paths else self.read_operation_count
        return max(0, total - len(self.memory_read_file_paths))

    @property
    def collapsed_search_count(self) -> int:
        return max(0, self.search_count - self.memory_search_count)

    @property
    def memory_read_count(self) -> int:
        return len(self.memory_read_file_paths) + len(self.relevant_memories)

    @property
    def non_mem_read_file_paths(self) -> list[str]:
        return [p for p in self.read_file_paths if p not in self.memory_read_file_paths]

    @property
    def displayed_hint(self) -> str | None:
        """The hint the row shows: the latest one, else the last read path or search pattern."""
        if self.latest_display_hint is not None:
            return self.latest_display_hint
        reads = self.non_mem_read_file_paths
        if reads:
            return get_display_path(reads[-1], self.context)
        return f'"{self.non_mem_search_args[-1]}"' if self.non_mem_search_args else None


def _verb(active: bool, first: bool, active_verb: str, done_verb: str) -> str:
    verb = active_verb if active else done_verb
    return verb[0].upper() + verb[1:] if first else verb


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def collapsed_summary_text(group: ReadSearchGroup, *, active: bool = False) -> str:
    """
    `CollapsedReadSearchContent`'s summary line: git operations (fullscreen), searches, reads,
    listings, MCP queries, bash commands (fullscreen), then memory operations, `…` while active.
    `""` when the group counts nothing.
    """
    fullscreen = is_fullscreen_env_enabled()
    parts: list[str] = []

    def push(active_verb: str, done_verb: str, body: str) -> None:
        parts.append(f"{_verb(active, not parts, active_verb, done_verb)} {body}")

    def push_fixed(verb: str, body: str) -> None:
        parts.append(f"{verb[0].upper() + verb[1:] if not parts else verb} {body}")

    if fullscreen:
        for kind, label in (("committed", "committed"), ("amended", "amended commit"),
                            ("cherry-picked", "cherry-picked")):
            shas = [c.sha for c in group.commits if c.kind == kind]
            if shas:
                push_fixed(label, ", ".join(shas))
        if group.pushes:
            push_fixed("pushed to", ", ".join(dict.fromkeys(p.branch for p in group.pushes)))
        for branch in group.branches:
            push_fixed("merged" if branch.action == "merged" else "rebased onto", branch.ref)
        pr_verbs = {"created": "created", "edited": "edited", "merged": "merged", "commented": "commented on",
                    "closed": "closed", "ready": "marked ready"}
        for pr in group.prs:
            push_fixed(pr_verbs[pr.action], f"PR #{pr.number}")
    search, read = group.collapsed_search_count, group.read_count
    if search > 0:
        push("searching for", "searched for", f"{search} {_plural(search, 'pattern', 'patterns')}")
    if read > 0:
        push("reading", "read", f"{read} {_plural(read, 'file', 'files')}")
    if group.list_count > 0:
        push("listing", "listed", f"{group.list_count} {_plural(group.list_count, 'directory', 'directories')}")
    if group.mcp_call_count > 0:
        label = ", ".join(re.sub(r"^claude\.ai ", "", n) for n in group.mcp_server_names) or "MCP"
        times = f" {group.mcp_call_count} times" if group.mcp_call_count > 1 else ""
        push("querying", "queried", f"{label}{times}")
    bash = max(0, group.bash_count - group.git_op_bash_count) if fullscreen else 0
    if bash > 0:
        push("running", "ran", f"{bash} bash {_plural(bash, 'command', 'commands')}")
    if group.memory_read_count > 0:
        count = group.memory_read_count
        push("recalling", "recalled", f"{count} {_plural(count, 'memory', 'memories')}")
    if group.memory_search_count > 0:
        parts.append(f"{_verb(active, not parts, 'searching', 'searched')} memories")
    if group.memory_write_count > 0:
        count = group.memory_write_count
        push("writing", "wrote", f"{count} {_plural(count, 'memory', 'memories')}")
    if not parts:
        return ""
    text = ", ".join(parts)
    return f"{text}…" if active else text


def get_search_read_summary_text(
    search_count: int, read_count: int, is_active: bool, repl_count: int = 0,
    memory_counts: Mapping[str, int] | None = None, list_count: int = 0,
) -> str:
    """`getSearchReadSummaryText`: memory operations first, then searches, reads, listings, REPL."""
    parts: list[str] = []

    def push(active_verb: str, done_verb: str, body: str) -> None:
        parts.append(f"{_verb(is_active, not parts, active_verb, done_verb)} {body}")

    if memory_counts:
        memory_read = memory_counts.get("memoryReadCount", 0)
        if memory_read > 0:
            push("recalling", "recalled", f"{memory_read} {_plural(memory_read, 'memory', 'memories')}")
        if memory_counts.get("memorySearchCount", 0) > 0:
            parts.append(f"{_verb(is_active, not parts, 'searching', 'searched')} memories")
        memory_write = memory_counts.get("memoryWriteCount", 0)
        if memory_write > 0:
            push("writing", "wrote", f"{memory_write} {_plural(memory_write, 'memory', 'memories')}")
    if search_count > 0:
        push("searching for", "searched for", f"{search_count} {_plural(search_count, 'pattern', 'patterns')}")
    if read_count > 0:
        push("reading", "read", f"{read_count} {_plural(read_count, 'file', 'files')}")
    if list_count > 0:
        push("listing", "listed", f"{list_count} {_plural(list_count, 'directory', 'directories')}")
    if repl_count > 0:
        parts.append(f"{'REPL' + chr(39) + 'ing' if is_active else 'REPL' + chr(39) + 'd'} {repl_count} "
                     f"{_plural(repl_count, 'time', 'times')}")
    text = ", ".join(parts)
    return f"{text}…" if is_active else text


def summarize_recent_activities(activities: Sequence[Mapping[str, Any]]) -> str | None:
    """
    `summarizeRecentActivities`: two or more trailing searches/reads roll up into one active
    summary; otherwise the latest activity that has a description.
    """
    if not activities:
        return None
    search_count = read_count = 0
    for activity in reversed(activities):
        if activity.get("isSearch"):
            search_count += 1
        elif activity.get("isRead"):
            read_count += 1
        else:
            break
    if search_count + read_count >= 2:
        return get_search_read_summary_text(search_count, read_count, True)
    for activity in reversed(activities):
        if activity.get("activityDescription"):
            return str(activity["activityDescription"])
    return None


__all__ = [
    "MAX_HINT_CHARS",
    "ReadSearchGroup",
    "SearchOrReadResult",
    "collapsed_summary_text",
    "command_as_hint",
    "get_search_read_summary_text",
    "get_tool_search_or_read_info",
    "is_memory_search",
    "is_memory_write_or_edit",
    "summarize_recent_activities",
]
