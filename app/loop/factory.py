import time
import uuid
from typing import Any

from app.loop.config import RunConfig
from app.loop.state import LoopState
from app.loop.deps import LoopDeps, CompactionResult, StopHookResult
from app.loop.model import stream_call_model
from app.loop.tools.orchestration import run_tools


async def production_compact(state: LoopState, config: RunConfig) -> CompactionResult:
    # A simplified passthrough until full compaction strategy is needed in factory
    return CompactionResult(compacted=False, messages=state.messages, tracking=state.compaction_tracking)


async def production_stop_hooks(state: LoopState, config: RunConfig) -> StopHookResult:
    return StopHookResult(prevent_continuation=False)


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
        stop_hooks=production_stop_hooks,
        uuid=production_uuid,
        now=production_now,
        emit_event=production_emit_event,
    )
