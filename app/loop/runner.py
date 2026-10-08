import uuid
import logging
from typing import Any

from app.loop.config import RunConfig
from app.loop.state import LoopState, CompactionTracking
from app.loop.context import tool_context_for, AppStateStore
from app.loop.engine import run_loop, drain
from app.loop.factory import get_production_deps
from app.loop.transitions import Terminal, Terminated
from app.loop.tools.pool import assemble_tool_pool
from app.session.state import PlanItemResult, SessionState

logger = logging.getLogger("loop_runner")

async def tdd_loop_runner(sub_req: str, idx: int, state: SessionState) -> PlanItemResult:
    """
    Executes a single sub-requirement using the robust Claude Code style 'while-loop'.
    """
    logger.info(f"🚀 Launching TDD Loop for sub_req: {sub_req}")
    run_id = f"loop-{uuid.uuid4().hex[:8]}"
    config = RunConfig(run_id=run_id, abort_signal=None)
    deps = get_production_deps()
    store = AppStateStore()
    
    # Initialize tools using the robust pool assembly mapped to the prompt registry
    from app.loop.tools.fs import (
        build_read_file_tool, 
        build_write_file_tool, 
        build_edit_tool, 
        build_glob_tool, 
        build_grep_tool
    )
    from app.loop.tools.bash import build_bash_tool
    from app.loop.tools.run_tests import build_run_tests_tool
    
    tool_vars = {
        "working_directory": str(Path.cwd()),
        "user_id": "tdd_user",
        "agent_name": "TDDAgent",
        "phase": str(store.get().phase_ledger.phase.value),
        "run_id": run_id,
    }
    builtins = [
        build_read_file_tool(vars=tool_vars), 
        build_write_file_tool(vars=tool_vars), 
        build_edit_tool(vars=tool_vars), 
        build_glob_tool(vars=tool_vars), 
        build_grep_tool(vars=tool_vars), 
        build_bash_tool(vars=tool_vars), 
        build_run_tests_tool(vars=tool_vars),
    ]
    tools = assemble_tool_pool(builtins, phase_ledger=store.get().phase_ledger)
    
    # Provide the orchestrator context with the prompt as a message
    from langchain_core.messages import HumanMessage
    initial_message = HumanMessage(content=sub_req)
    
    ctx = tool_context_for(store=store, tools=tools, messages=(initial_message,))
    loop_state = LoopState(
        messages=(initial_message,),
        tool_context=ctx,
        phase_ledger=store.get().phase_ledger,
        compaction_tracking=CompactionTracking(message_id_range=(0,0)),
        turn_count=0
    )

    from app.loop.transcript import TranscriptLogger, with_transcript_logger
    transcript_logger = TranscriptLogger(run_id=run_id)
    
    try:
        events = with_transcript_logger(run_loop(loop_state, config, deps), transcript_logger)
        terminal_event = await drain(events)
        reason = terminal_event.reason
    except Exception as e:
        logger.error(f"Loop crashed: {e}")
        reason = Terminal.MODEL_ERROR
        
    final_ledger = store.get().phase_ledger
    
    from app.loop.model import _global_token_tracker
    token_usage = _global_token_tracker.summary()
    
    status_str = "success" if reason == Terminal.COMPLETED else "failed"
    logger.info(
        f"📊 Resilience Log [Sub-req {idx}]: Status={status_str}, "
        f"Reason={reason}, RED={final_ledger.red_confirmed}, GREEN={final_ledger.green_passed}, "
        f"Tokens={token_usage['totals']['total_tokens']}"
    )
    
    return PlanItemResult(
        index=idx,
        sub_requirement=sub_req,
        status="success" if reason == Terminal.COMPLETED else "failed",
        terminal_reason=str(reason),
        red_confirmed=final_ledger.red_confirmed,
        green_passed=final_ledger.green_passed,
        error_message=None if reason == Terminal.COMPLETED else f"Terminated early: {reason}"
    )
