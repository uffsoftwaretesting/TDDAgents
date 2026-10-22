"""
Unit tests for Plan C: Persistence & The Memdir System.

Verifies:
1. Append-oriented memdir transcript logger serializes LoopState checkpoints.
"""


from langchain_core.messages import AIMessage, HumanMessage

from app.loop.context import AppState, AppStateStore, CancelToken, ToolContext
from app.loop.ledger import PhaseLedger, TddPhase
from app.loop.state import LoopState
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
