"""
The agent definition (`reference/claude-code/src/tools/AgentTool/loadAgentsDir.ts` ->
`AgentDefinition` / `CustomAgentDefinition`), as `parseAgentFromMarkdown` builds it.

Field for field upstream's where a subsystem exists here: `agentType` is `name`, `whenToUse`
is `description`, and `getSystemPrompt()` is the parsed body in `prompt`. Two fields are
TDDAgents' own: `phase`, the one field whose invalid value fails a load loudly (silently
dropping it would silently disable the TDD invariant), and the `run`/`session`/`none` memory
scopes (run-scoped memory, Part I5).

Not ported, because their subsystems do not exist here: `color`, `effort`, `isolation`,
`mcpServers`, `requiredMcpServers`, `skills`, `initialPrompt`, `criticalSystemReminder`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.loop.ledger import TddPhase


class AgentFrontmatterError(ValueError):
    """Raised when an agent definition carries an invalid load-breaking field (such as phase)."""


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """
    A parsed agent. Omission means unset (`None`); an invalid value is logged and ignored.
    """

    name: str
    description: str
    prompt: str
    phase: TddPhase | None = None
    #: `undefined` = every tool, `()` = none (`parseAgentToolsFromFrontmatter`).
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] | None = None
    #: One of upstream's `PERMISSION_MODES`; unset means the spawn default (`acceptEdits`).
    permission_mode: str | None = None
    memory: str | None = None
    #: Upstream's own optional ceiling; unset is unbounded.
    max_turns: int | None = None
    hooks: dict[str, Any] | None = None
    #: A model id, or the `inherit` sentinel.
    model: str | None = None
    background: bool | None = None
    #: Built-in agents only (upstream sets it on Explore and Plan): drop `TDDAGENTS.md` from
    #: the worker's user context.
    omit_claude_md: bool = False
    #: Kept for `app/loop/prompts/loader.py`'s duplicate parser; nothing reads them.
    fork_from: str | None = None
    revert_on_red: bool | None = None
    source: str = "built-in"  # "built-in", "user", "project", "synthetic"
    base_dir: str = ""
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
