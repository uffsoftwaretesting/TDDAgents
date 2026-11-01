"""
Agent memory: where each scope lives, its prompt, and the run-scoped purge.

Ported from `reference/claude-code/src/tools/AgentTool/agentMemory.ts` -> `getAgentMemoryDir`,
`getAgentMemoryEntrypoint`, `isAgentMemoryPath`, `loadAgentMemoryPrompt` (the prompt is memdir's
`buildMemoryPrompt`, `app/memdir/memdir.py`). Upstream injects it only when auto memory is on
(`agent_tool.py::agent_system_prompt`, user decision 2026-11-01).

TDDAgents' additions (Part I5, §4.5): the `run` scope, discarded when the run ends so every run
stays an independent sample, and the `session` scope; `none` means no memory.
"""

from __future__ import annotations

import logging
import os
import posixpath
import shutil
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

AgentMemoryScope = Literal["run", "session", "local", "project", "user", "none"]

#: `loadAgentMemoryPrompt`'s display name.
AGENT_MEMORY_DISPLAY_NAME = "Persistent Agent Memory"

#: `loadAgentMemoryPrompt`'s scope notes: upstream's three, verbatim, plus the two TDDAgents scopes
#: (run, session) that Part I5 adds.
SCOPE_NOTES: dict[str, str] = {
    "user": "- Since this memory is user-scope, keep learnings general since they apply across all projects",
    "project": ("- Since this memory is project-scope and shared with your team via version control, tailor your "
                "memories to this project"),
    "local": ("- Since this memory is local-scope (not checked into version control), tailor your memories to this "
              "project and machine"),
    "run": ("- Since this memory is run-scope (discarded when this run ends, so every run stays an independent "
            "sample), keep only what helps the rest of this run"),
    "session": ("- Since this memory is session-scope (kept for this session only), tailor your memories to this "
                "session's work"),
}


class AgentMemoryStore:
    """Manages memory directories, prompts, and run-scoped isolation for agents."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        user_home: Path | str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.user_home = Path(user_home or Path.home()).resolve()

    def get_memory_dir(
        self,
        agent_type: str,
        scope: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> Path | None:
        """Resolve the memory directory path for a given agent and scope."""
        norm_scope = scope.strip().lower()
        safe_agent = agent_type.strip().replace(":", "-").replace("/", "-")

        if norm_scope == "none":
            return None
        elif norm_scope == "run":
            # No shared fallback: a run id that silently became "default_run" would let one
            # run read another's scratchpad, which is exactly what run scope forbids (I5).
            if not run_id:
                raise ValueError("Run-scoped agent memory needs a run id")
            return self.base_dir / ".tddagents" / "run-memory" / run_id / safe_agent
        elif norm_scope == "session":
            sid = session_id or "default_session"
            return self.base_dir / ".tddagents" / "session-memory" / sid / safe_agent
        elif norm_scope == "local":
            return self.base_dir / ".tddagents" / "agent-memory-local" / safe_agent
        elif norm_scope == "project":
            return self.base_dir / ".tddagents" / "agent-memory" / safe_agent
        elif norm_scope == "user":
            return self.user_home / ".tddagents" / "agent-memory" / safe_agent
        else:
            logger.warning("Unrecognized memory scope '%s', treating as none", scope)
            return None

    def ensure_memory_dir_exists(
        self,
        agent_type: str,
        scope: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> Path | None:
        """Create the memory directory if it does not already exist."""
        mdir = self.get_memory_dir(agent_type, scope, run_id=run_id, session_id=session_id)
        if mdir is not None:
            mdir.mkdir(parents=True, exist_ok=True)
        return mdir

    def get_entrypoint(
        self,
        agent_type: str,
        scope: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> Path | None:
        """Return the path to MEMORY.md for the agent."""
        mdir = self.get_memory_dir(agent_type, scope, run_id=run_id, session_id=session_id)
        if mdir is None:
            return None
        return mdir / "MEMORY.md"

    def load_memory_prompt(
        self,
        agent_type: str,
        scope: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """
        `agentMemory.ts` -> `loadAgentMemoryPrompt`: the directory is created, then
        `buildMemoryPrompt('Persistent Agent Memory', dir, [scope note])` with its MEMORY.md.
        """
        norm_scope = scope.strip().lower()
        mdir = self.ensure_memory_dir_exists(agent_type, norm_scope, run_id=run_id, session_id=session_id)
        if mdir is None:
            return ""
        from app.memdir.memdir import build_memory_prompt

        return build_memory_prompt(AGENT_MEMORY_DISPLAY_NAME, f"{mdir}{os.sep}", [SCOPE_NOTES[norm_scope]])

    def memory_roots(self, run_id: str | None) -> tuple[str, ...]:
        """
        The directories `isAgentMemoryPath` accepts: this run's run-scoped memory (never another
        run's, so run isolation (I5) holds), then the session, local, project and user scopes.
        """
        tdd = self.base_dir / ".tddagents"
        roots = [tdd / "session-memory", tdd / "agent-memory-local", tdd / "agent-memory",
                 self.user_home / ".tddagents" / "agent-memory"]
        if run_id:
            roots.insert(0, tdd / "run-memory" / run_id)
        return tuple(posixpath.normpath(str(root)) for root in roots)

    def is_memory_path(self, absolute_path: str, run_id: str | None) -> bool:
        """`isAgentMemoryPath`: the path, normalized against `..`, lies under one of `memory_roots`."""
        normalized = posixpath.normpath(absolute_path)
        return any(normalized.startswith(root + "/") for root in self.memory_roots(run_id))

    def discard_run_memory(self, run_id: str) -> None:
        """
        Purge the entire run-scoped memory directory for a specific run ID.

        Enforces I5: every run stays a completely independent evaluation sample.
        """
        run_dir = self.base_dir / ".tddagents" / "run-memory" / run_id
        if run_dir.is_dir():
            try:
                shutil.rmtree(run_dir)
                logger.info("Purged run-scoped agent memory for run %s at %s", run_id, run_dir)
            except OSError as e:
                logger.warning("Failed purging run memory %s: %s", run_dir, e)


def agent_memory_target(context: Any, raw_path: str) -> str | None:
    """
    The host path of `raw_path` when it names this run's agent memory, else `None`.

    Upstream's agents read and write their memory with the ordinary file tools, and
    `checkEditableInternalPath` / `checkReadableInternalPath` allow it (`isAgentMemoryPath`). The
    store lives outside the workspace on purpose (I5), so the file tools reach it on the host,
    as they read a persisted tool result. Only an absolute path counts: the memory prompt names
    the directory absolutely, and a relative path is always the project's.
    """
    store = getattr(context, "agent_memory_store", None)
    expanded = os.path.expanduser(str(raw_path or ""))
    if store is None or not posixpath.isabs(expanded):
        return None
    normalized = posixpath.normpath(expanded)
    return normalized if store.is_memory_path(normalized, getattr(context, "run_id", None) or None) else None
