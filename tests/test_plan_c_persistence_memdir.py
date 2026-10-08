"""
Unit tests for Plan C: Persistence & The Memdir System.

Verifies:
1. Append-oriented memdir transcript logger serializes LoopState checkpoints.
2. Context microcompaction and tool result budget in build_request_messages.
3. Cross-session auto-memory extraction and MEMORY.md updates within size caps.
"""

import json
from pathlib import Path
import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage

from app.loop.config import build_run_config
from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext
from app.loop.context.compact import apply_tool_result_budget, microcompact_tool_results
from app.loop.context.memory import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    extract_session_memory,
    get_auto_mem_entrypoint,
    get_memory_files,
    is_auto_memory_enabled,
    update_auto_memory,
)
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.model import build_request_messages
from app.loop.state import LoopState, initial_loop_state
from app.loop.transcript import TranscriptLogger
from app.loop.transitions import Continue, Transition


def _make_context(messages=(), tools=(), phase=TddPhase.RED) -> ToolContext:
    ledger = PhaseLedger(phase=phase)
    store = AppStateStore(AppState(phase_ledger=ledger))
    return ToolContext(
        cancel=CancelToken(),
        get_app_state=store.get,
        set_app_state=store.update,
        messages=tuple(messages),
        tools=tuple(tools),
    )


# ── Plan C.1: Session Checkpointing via TranscriptLogger ─────────────────────

def test_transcript_logger_checkpoints_loop_state(tmp_path):
    """TranscriptLogger records LoopState checkpoints in append-only JSONL format."""
    logger = TranscriptLogger(run_id="run_123", base_dir=tmp_path)
    ctx = _make_context()
    ledger = PhaseLedger(phase=TddPhase.GREEN, red_confirmed=True)

    state = LoopState(
        messages=(HumanMessage(content="Hello"), AIMessage(content="Hi")),
        tool_context=ctx,
        phase_ledger=ledger,
        compaction_tracking=None,
        has_attempted_reactive_compact=False,
        stop_hook_active=None,
        turn_count=2,
        transition=Transition(reason=Continue.NEXT_TURN),
    )

    logger.checkpoint_state(state)

    events = logger.read_events()
    assert len(events) == 1
    cp = events[0]
    assert cp["type"] == "state_checkpoint"
    assert cp["turn_count"] == 2
    assert cp["phase"] == "GREEN"
    assert cp["transition"] == "next_turn"

    # Verify latest checkpoint retrieval
    last_cp = logger.get_last_checkpoint()
    assert last_cp is not None
    assert last_cp["turn_count"] == 2


# ── Plan C.2: Context Compaction Activation ──────────────────────────────────

def test_microcompact_and_tool_result_budget():
    """microcompact_tool_results and apply_tool_result_budget shrink old tool outputs."""
    # Build 3 turns of conversation with large tool outputs
    large_output = "x" * 20_000
    messages = [
        HumanMessage(content="turn 1"),
        AIMessage(content="calling tool"),
        ToolMessage(content=large_output, tool_call_id="call_1"),
        HumanMessage(content="turn 2"),
        AIMessage(content="calling tool"),
        ToolMessage(content=large_output, tool_call_id="call_2"),
        HumanMessage(content="turn 3"),
        AIMessage(content="calling tool"),
        ToolMessage(content="recent output", tool_call_id="call_3"),
    ]

    # Microcompaction keeps last 2 turns, collapses older
    compacted = microcompact_tool_results(messages, keep_recent_turns=2)
    assert "[Tool result microcompacted:" in str(compacted[2].content)
    # Most recent tool result is untouched
    assert compacted[-1].content == "recent output"

    # Budget truncates oversized results
    budgeted = apply_tool_result_budget(compacted, max_chars_per_result=1_000)
    assert len(str(budgeted[5].content)) <= 1_200


def test_build_request_messages_applies_compaction(tmp_path, monkeypatch):
    """build_request_messages applies microcompaction and budget on state messages."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config = build_run_config(run_id="compaction_test", postgres_checkpointing=False)

    large_output = "data " * 5_000
    history = [
        HumanMessage(content="old request"),
        AIMessage(content="tool call"),
        ToolMessage(content=large_output, tool_call_id="call_old"),
        HumanMessage(content="recent request 1"),
        AIMessage(content="recent call 1"),
        ToolMessage(content="recent 1", tool_call_id="call_rec_1"),
        HumanMessage(content="recent request 2"),
        AIMessage(content="recent call 2"),
        ToolMessage(content="recent 2", tool_call_id="call_rec_2"),
    ]

    ctx = _make_context(messages=history, phase=TddPhase.GREEN)
    state = initial_loop_state(messages=tuple(history), tool_context=ctx)

    request_msgs = build_request_messages(state, config)
    # Old tool message should have been microcompacted in request messages
    found_microcompacted = any(
        isinstance(m, ToolMessage) and "[Tool result microcompacted:" in str(m.content)
        for m in request_msgs
    )
    assert found_microcompacted is True


# ── Plan C.3: Cross-Session Auto-Memory Extraction ───────────────────────────

def test_extract_and_update_auto_memory(tmp_path):
    """Auto-memory extraction saves architectural patterns to MEMORY.md."""
    messages = [
        HumanMessage(content="Always use pytest instead of unittest and keep tests async."),
        AIMessage(content="Understood. I will use pytest and async tests."),
        HumanMessage(content="Make sure all file paths in outputs use GitHub links."),
    ]

    memories = extract_session_memory(messages)
    assert len(memories) >= 1
    assert any("pytest" in m.lower() for m in memories)

    project_name = "test_project"
    updated_content = update_auto_memory(
        project=project_name,
        new_memories=memories,
        home=str(tmp_path),
    )

    entrypoint_path = Path(get_auto_mem_entrypoint(project_name, home=str(tmp_path)))
    assert entrypoint_path.is_file()
    assert "pytest" in entrypoint_path.read_text(encoding="utf-8").lower()

    # Verify line and byte limits are respected
    assert len(updated_content.splitlines()) <= MAX_ENTRYPOINT_LINES + 5
    assert len(updated_content.encode("utf-8")) <= MAX_ENTRYPOINT_BYTES + 500
