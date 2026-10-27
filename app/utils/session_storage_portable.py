"""
Where a run's transcripts live (Phase 6).

Ported from `reference/claude-code/src/utils/sessionStorage.ts` -> `getProjectsDir`,
`getTranscriptPath`, `getAgentTranscriptPath` (with `setAgentTranscriptSubdir`'s grouping as
an explicit `subdir` argument rather than a module-level map), and
`reference/claude-code/src/utils/sessionStoragePortable.ts` -> `getProjectDir`:

    <config>/projects/<sanitized cwd>/<session>.jsonl
    <config>/projects/<sanitized cwd>/<session>/subagents[/<subdir>]/agent-<id>.jsonl

The project directory is the same one persisted tool results already use
(`tools/result_storage.py` -> `tool_results_dir`), so a run's record sits in one place, outside
the workspace, where no agent can read or edit it.
"""

from __future__ import annotations

from pathlib import Path

from app.utils.tool_result_storage import config_home, sanitize_path


def get_projects_dir() -> Path:
    """`getProjectsDir`."""
    return config_home() / "projects"


def get_project_dir(cwd: str | Path) -> Path:
    """`getProjectDir`."""
    return get_projects_dir() / sanitize_path(str(cwd))


def get_transcript_path(session_id: str, cwd: str | Path) -> Path:
    """`getTranscriptPath`."""
    return get_project_dir(cwd) / f"{session_id}.jsonl"


def get_agent_transcript_path(session_id: str, agent_id: str, cwd: str | Path, subdir: str | None = None) -> Path:
    """`getAgentTranscriptPath`."""
    base = get_project_dir(cwd) / session_id / "subagents"
    if subdir:
        base = base / subdir
    return base / f"agent-{agent_id}.jsonl"


__all__ = ["get_agent_transcript_path", "get_project_dir", "get_projects_dir", "get_transcript_path"]
