"""
HostRead — read-only access to arbitrary paths on the developer's machine.

**No path allowlist, by an explicit decision.** An allowlist here would be theatre: the
same agents that could hold HostRead can hold a read-capable Bash on the same host, so a
list of blessed prefixes would describe a boundary that does not exist. The real boundary
is the `workspace:` field on the agent definition, enforced by the workspace gate in
`execute_tool` — this tool declares `required_workspace: local`, so a sandbox-pinned agent
is refused it even if a frontmatter typo puts it in their tool list.

It is also the one tool that is not written against `Workspace`. `LocalWorkspace` is rooted
at `.tddagents/runs/<thread_id>/workspace` and rejects `..` by design; the whole purpose of
HostRead is to reach outside that root — to read installed package source, a config file,
or this project's own documentation.

Read-only, always. There is deliberately no HostWrite.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.tools.base import (
    AnyTool,
    Capability,
    ToolContext,
    ToolResult,
    build_tool,
    err,
    ok,
)

#: Same paging contract as ReadFile, for the same reason.
DEFAULT_HOST_READ_LINES = 2000
MAX_HOST_FILE_BYTES = 5_000_000


class HostReadArgs(BaseModel):
    path: str = Field(description="An absolute path on the host, or one relative to the repo.")
    offset: int = Field(default=0, description="First line to return, 0-based.")
    limit: int = Field(default=DEFAULT_HOST_READ_LINES, description="How many lines to return.")


HOST_READ_PROMPT = """Reads a text file from the host machine, outside the workspace.

Use this for things the workspace does not contain: an installed library's source, a
reference document elsewhere on disk, this project's own configuration. For files inside
the workspace, use ReadFile — it works against whichever environment you are pointed at.

Read-only. Output is line-numbered exactly as ReadFile's is, and paged the same way."""


def _host_read(args: HostReadArgs, ctx: ToolContext) -> ToolResult:
    target = Path(args.path).expanduser()

    try:
        resolved = target.resolve()
    except OSError as exc:
        return err(f"Could not resolve {args.path}: {exc}")

    if not resolved.exists():
        return err(f"No such file: {args.path}")
    if resolved.is_dir():
        return err(f"{args.path} is a directory. Name a file.")

    try:
        if resolved.stat().st_size > MAX_HOST_FILE_BYTES:
            return err(
                f"{args.path} is larger than {MAX_HOST_FILE_BYTES} bytes; refusing to "
                f"read it whole. Narrow it with Bash if you really need this file."
            )
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return err(f"Could not read {args.path}: {exc}")

    lines = content.splitlines()
    if not lines:
        return ok(f"{args.path} is empty.")

    start = max(args.offset, 0)
    window = lines[start:start + max(args.limit, 0)]
    if not window:
        return ok(f"{args.path} has {len(lines)} lines; offset {start} is past the end.")

    width = len(str(start + len(window)))
    rendered = "\n".join(f"{start + i + 1:>{width}}\t{line}" for i, line in enumerate(window))

    remaining = len(lines) - (start + len(window))
    if remaining > 0:
        rendered += f"\n\n... {remaining} more line(s). Continue with offset={start + len(window)}."

    return ok(rendered)


HostRead = build_tool(
    name="HostRead",
    args_schema=HostReadArgs,
    prompt=HOST_READ_PROMPT,
    call=_host_read,
    description=lambda args: f"Host-read {args.path}",
    is_read_only=lambda args: True,
    is_concurrency_safe=lambda args: True,
    required_capability=lambda args: Capability.READ,
    required_workspace=lambda args: "local",
)

HOST_TOOLS: list[AnyTool] = [HostRead]
