"""
TDD Orchestrator coordinating session execution conforming to Part K and L.

Delegates TDD loop execution and plan item iteration to `SessionShell`
while maintaining backwards-compatible AgentState outputs.
"""

from __future__ import annotations

import logging

from app.config.config import AgentState
from app.session.checkpointer import build_checkpointer
from app.session.shell import SessionShell
from app.loop.runner import tdd_loop_runner
from app.session.state import SessionStatus
from app.utils.token_metrics import GlobalTokenTracker

logger = logging.getLogger("TDDOrchestrator")


class TDDOrchestrator:
    """
    Coordinates TDD execution over sub-requirements using SessionShell.
    """

    def __init__(self, task_key: str = "tdd_task") -> None:
        self.task_key = task_key
        self.token_tracker = GlobalTokenTracker()
        self._shell = SessionShell(checkpointer=build_checkpointer(), loop_runner=tdd_loop_runner)

    def run(self, specification: str, requirements: str) -> AgentState:
        """
        Executes the TDD plan items under the unified loop engine.
        """
        self.token_tracker.reset()
        logger.info("\n" + "#" * 80)
        logger.info("🚀 STARTING TDD ORCHESTRATOR (SessionShell + Unified Loop Engine)")
        logger.info("#" * 80 + "\n")

        # Initial prompt / task execution
        result = self._shell.run(
            initial_input=requirements or specification,
            thread_id=self.task_key,
            specification=specification,
        )

        # If interrupted on requirements analyst, approve to proceed with planning & iteration
        if result.get("status") == SessionStatus.INITIALIZING:
            result = self._shell.resume(self.task_key, "/yes")

        status_str = "completed" if result.get("status") == SessionStatus.COMPLETED else "failed"

        # Adapt SessionState to AgentState schema
        plan = list(result.get("plan") or [])
        subreq_results = list(result.get("subreq_results") or [])
        success_count = int(result.get("success_count") or 0)
        failure_count = int(result.get("failure_count") or 0)

        agent_state: AgentState = {
            "specification": specification,
            "requirements": requirements,
            "plan": plan,
            "plan_index": int(result.get("plan_index") or len(plan)),
            "current_sub_req": str(result.get("current_sub_req") or ""),
            "sandbox_id": "session-sandbox",
            "file_system": {},
            "tester_messages": [],
            "developer_messages": [],
            "reviewer_messages": [],
            "audit_log": list(result.get("audit_log") or []),
            "iteration": 1,
            "infra_retries": 0,
            "status": status_str,
            "max_retries": 15,
            "failed_requirements": [s for s in subreq_results if s.get("status") == "failed"],
            "total_detected_failures": failure_count,
            "autonomously_corrected_failures": 0,
            "current_subreq_failures": 0,
            "is_type_fault": "",
            "test_faults": 0,
            "implementation_faults": 0,
            "subreq_success_count": success_count,
            "subreq_failure_count": failure_count,
            "subreq_results": subreq_results,
            "is_flow_type": [],
        }

        return agent_state
