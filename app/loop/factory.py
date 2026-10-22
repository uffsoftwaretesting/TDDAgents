import time
import uuid
from typing import Any

from app.loop.config import RunConfig
from app.loop.deps import CompactionResult, LoopDeps
from app.loop.hooks.stop_hooks import build_stop_hooks_runner
from app.loop.model import stream_call_model
from app.loop.state import LoopState
from app.loop.tools.orchestration import run_tools


async def production_compact(state: LoopState, config: RunConfig) -> CompactionResult:
    # A simplified passthrough until full compaction strategy is needed in factory
    return CompactionResult(compacted=False, messages=state.messages, tracking=state.compaction_tracking)


def production_uuid() -> str:
    return str(uuid.uuid4())


def production_now() -> float:
    return time.time()


def production_emit_event(event: Any) -> None:
    pass  # Extend as necessary for telemetry


def get_production_deps() -> LoopDeps:
    """Builds the real dependencies for the engine."""
    return LoopDeps(
        call_model=stream_call_model,
        run_tools=run_tools,
        compact=production_compact,
        # Barrier 3: `tdd_phase_incomplete_hook` first, then the configured Stop hooks,
        # which can add a block but never clear one (`app/loop/hooks/stop_hooks.py`).
        stop_hooks=build_stop_hooks_runner(),
        uuid=production_uuid,
        now=production_now,
        emit_event=production_emit_event,
    )
