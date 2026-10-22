"""Phase 6: where transcripts live (`sessionStorage.ts` -> `getTranscriptPath`,
`getAgentTranscriptPath`; `sessionStoragePortable.ts` -> `getProjectDir`)."""

from __future__ import annotations

from app.loop.storage.paths import (
    get_agent_transcript_path,
    get_project_dir,
    get_projects_dir,
    get_transcript_path,
)
from app.loop.tools.result_storage import sanitize_path, tool_results_dir


def test_the_projects_dir_is_under_the_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert get_projects_dir() == tmp_path / "projects"


def test_the_project_dir_is_the_sanitized_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert get_project_dir("/home/u/ws") == tmp_path / "projects" / "-home-u-ws"
    long_cwd = "/" + "a" * 300
    assert get_project_dir(long_cwd) == tmp_path / "projects" / sanitize_path(long_cwd)


def test_the_main_transcript_is_named_after_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert get_transcript_path("s-1", "/w") == tmp_path / "projects" / "-w" / "s-1.jsonl"


def test_a_worker_transcript_lives_under_the_session_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    base = tmp_path / "projects" / "-w" / "s-1" / "subagents"
    assert get_agent_transcript_path("s-1", "a7", "/w") == base / "agent-a7.jsonl"
    assert get_agent_transcript_path("s-1", "a7", "/w", subdir="workflows/r1") == (
        base / "workflows" / "r1" / "agent-a7.jsonl"
    )


def test_tool_results_share_the_session_dir(tmp_path, monkeypatch):
    """Persisted tool results (Phase 2) and worker transcripts sit side by side, as upstream."""
    monkeypatch.setenv("TDDAGENTS_CONFIG_DIR", str(tmp_path))
    assert tool_results_dir("s-1", "/w").parent == get_agent_transcript_path("s-1", "a", "/w").parent.parent
