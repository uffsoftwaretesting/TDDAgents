"""
Unit tests for I5: Agent memory management and run-scoped cleanup.
"""

from pathlib import Path

from app.loop.agents.memory import AgentMemoryStore


def test_get_memory_dir_scopes(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path / "proj", user_home=tmp_path / "user")

    run_dir = store.get_memory_dir("developer", "run", run_id="run_123")
    assert run_dir == tmp_path / "proj" / ".tddagents" / "run-memory" / "run_123" / "developer"

    session_dir = store.get_memory_dir("developer", "session", session_id="sess_456")
    assert session_dir == tmp_path / "proj" / ".tddagents" / "session-memory" / "sess_456" / "developer"

    local_dir = store.get_memory_dir("developer", "local")
    assert local_dir == tmp_path / "proj" / ".tddagents" / "agent-memory-local" / "developer"

    proj_dir = store.get_memory_dir("developer", "project")
    assert proj_dir == tmp_path / "proj" / ".tddagents" / "agent-memory" / "developer"

    user_dir = store.get_memory_dir("developer", "user")
    assert user_dir == tmp_path / "user" / ".tddagents" / "agent-memory" / "developer"

    none_dir = store.get_memory_dir("developer", "none")
    assert none_dir is None

    invalid_dir = store.get_memory_dir("developer", "unsupported_scope")
    assert invalid_dir is None


def test_ensure_memory_dir_exists_and_entrypoint(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path)
    mdir = store.ensure_memory_dir_exists("tester", "run", run_id="r1")
    assert mdir is not None
    assert mdir.is_dir()

    entrypoint = store.get_entrypoint("tester", "run", run_id="r1")
    assert entrypoint == mdir / "MEMORY.md"


def test_load_memory_prompt_empty_and_populated(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path)

    # Empty prompt
    prompt_run = store.load_memory_prompt("refactorer", "run", run_id="r2")
    assert "# Agent Persistent Memory" in prompt_run
    assert "run-scoped" in prompt_run
    assert "scientific reproducibility" in prompt_run
    assert "Existing Memory Notes" not in prompt_run

    # Write notes to MEMORY.md
    entrypoint = store.get_entrypoint("refactorer", "run", run_id="r2")
    assert entrypoint is not None
    entrypoint.write_text("Discovered architecture pattern X.", encoding="utf-8")

    prompt_with_notes = store.load_memory_prompt("refactorer", "run", run_id="r2")
    assert "Existing Memory Notes:" in prompt_with_notes
    assert "Discovered architecture pattern X." in prompt_with_notes

    # 'none' scope returns empty
    assert store.load_memory_prompt("refactorer", "none") == ""


def test_discard_run_memory_purges_directory(tmp_path: Path) -> None:
    store = AgentMemoryStore(base_dir=tmp_path)
    run_id = "eval_sample_42"

    mdir = store.ensure_memory_dir_exists("developer", "run", run_id=run_id)
    assert mdir is not None
    entrypoint = mdir / "MEMORY.md"
    entrypoint.write_text("Intermediate thoughts", encoding="utf-8")
    scratch = mdir / "scratch.py"
    scratch.write_text("print('test')", encoding="utf-8")

    assert entrypoint.is_file()
    assert scratch.is_file()

    # Discard memory
    store.discard_run_memory(run_id)

    # Verify completely purged
    run_dir = tmp_path / ".tddagents" / "run-memory" / run_id
    assert not run_dir.exists()

    # Calling again on missing dir does not raise error
    store.discard_run_memory(run_id)
