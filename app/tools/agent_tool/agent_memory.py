"""
Agent memory management and run-scoped lifecycle cleanup.

Implements I5 conforming to §4.5:
- Run-scoped agent memory discarded at run end so each run stays an independent sample.
- Scopes: 'run', 'session', 'local', 'project', 'user', 'none'.
- Memory prompt generation and entrypoint file (MEMORY.md).
- Automatic directory creation and complete run-memory purge.
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
        Generate memory prompt section to inject into the agent's system instructions.
        """
        norm_scope = scope.strip().lower()
        mdir = self.ensure_memory_dir_exists(agent_type, norm_scope, run_id=run_id, session_id=session_id)
        if mdir is None:
            return ""

        entrypoint = mdir / "MEMORY.md"
        existing_notes = ""
        if entrypoint.is_file():
            try:
                existing_notes = entrypoint.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Failed reading agent memory from %s: %s", entrypoint, e)

        scope_desc = {
            "run": "run-scoped (scratchpad discarded at run end to guarantee sample independence)",
            "session": "session-scoped (preserved across queries within this active session)",
            "local": "local-scoped (persisted on this machine, untracked by VCS)",
            "project": "project-scoped (shared via version control)",
            "user": "user-scoped (universal across all projects on this machine)",
        }[norm_scope]

        memory_block = [
            "# Agent Persistent Memory",
            f"You have access to a {scope_desc} memory store at: {mdir}",
            f"Entrypoint file: {entrypoint}",
            (
                "- Use your file writing/editing tools to update MEMORY.md with "
                "key architectural findings, patterns, or decisions."
            ),
        ]

        if norm_scope == "run":
            memory_block.append(
                "- NOTE: This memory is strictly run-scoped and will be purged upon "
                "run completion to maintain scientific reproducibility."
            )

        if existing_notes:
            memory_block.extend([
                "",
                "## Existing Memory Notes:",
                existing_notes,
            ])

        return "\n".join(memory_block)

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
