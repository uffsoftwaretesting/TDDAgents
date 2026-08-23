from __future__ import annotations

import logging

from app.config.config import Config
from app.errors.sandbox.handler import handle_workspace_exception
from app.sandbox.adapter import E2BAdapter
from app.workspace.base import WorkspaceError

logger = logging.getLogger("TDDOrchestrator.Runner")

_PYTEST_FLAGS = "-vv -rA --tb=long --showlocals -W default -o asyncio_default_fixture_loop_scope=function -o asyncio_mode=auto"

def run_pytest_in_sandbox(sandbox_id: str, test_path: str = ".", is_red_phase: bool = False) -> tuple[str, bool]:
    """
    Returns:
        (output: str, is_success: bool)
    Raises:
        TransientInfraError: If there's a timeout or instability.
        FatalInfraError: If the sandbox expired or a serious error occurred.
    """
    logger.info(f"🏃 RUNNER: Running tests in Sandbox {sandbox_id[:8]}...")

    try:
        adapter = E2BAdapter.connect(sandbox_id)

        # Ensures pytest is available
        adapter.execute(
            "python -c 'import pytest' 2>/dev/null || pip install pytest -q",
        )

        cmd = f'PYTHONPATH=. python -m pytest "{test_path}" {_PYTEST_FLAGS}'
        # A test run is the largest and slowest command in the pipeline, so it gets its
        # own budget rather than the general command timeout.
        result = adapter.execute(cmd, timeout=Config.TEST_TIMEOUT)

        # A non-zero exit is the expected TDD observation, not an infrastructure
        # failure — the adapter returns it as a result and the branch below reads it.
        if result.exit_code != 0:
            output = f"--- STDOUT ---\n{result.stdout}\n--- STDERR ---\n{result.stderr}"

            if is_red_phase:
                logger.info("🔴 RUNNER: Tests failed (expected TDD behavior).")
            else:
                logger.info("🟢 RUNNER: Tests failed validation.")

            return output, False

        logger.info("✅ RUNNER: All tests passed.")
        return result.stdout or "Tests passed with no output", True

    except WorkspaceError as exc:
        handle_workspace_exception(exc, context="Runner")

    except Exception as exc:
        handle_workspace_exception(exc, context="Generic Runner")
