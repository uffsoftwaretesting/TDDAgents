from __future__ import annotations

import logging

from e2b import (
    Sandbox,
    SandboxException,
)

from app.config.config import Config
from app.errors.sandbox.handler import handle_e2b_exception

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
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)

        # Ensures pytest is available
        sandbox.commands.run(
            "python -c 'import pytest' 2>/dev/null || pip install pytest -q",
            user="root",
        )

        cmd = f'PYTHONPATH=. python -m pytest "{test_path}" {_PYTEST_FLAGS}'
        result = sandbox.commands.run(
            cmd,
            user="root"
        )

        logger.info("✅ RUNNER: All tests passed.")
        return result.stdout or "Tests passed with no output", True

    except SandboxException as exc:
        # 1. Check whether this is the expected TDD error (exit_code != 0)
        if type(exc).__name__ == "CommandExitException":
            output = f"--- STDOUT ---\n{getattr(exc, 'stdout', '')}\n--- STDERR ---\n{getattr(exc, 'stderr', '')}"

            if is_red_phase:
                logger.info("🔴 RUNNER: Tests failed (expected TDD behavior).")
            else:
                logger.info("🟢 RUNNER: Tests failed validation.")

            return output, False

        # 2. For any other sandbox error, forward it to the mapper to classify and raise
        handle_e2b_exception(exc, context="Runner")

    except Exception as exc:
        handle_e2b_exception(exc, context="Generic Runner")
