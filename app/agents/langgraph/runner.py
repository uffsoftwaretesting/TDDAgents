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
_PYTEST_TIMEOUT: int = 300
_REQUEST_TIMEOUT: int = 320

def run_pytest_in_sandbox(sandbox_id: str, test_path: str = ".", is_red_phase: bool = False) -> tuple[str, bool]:
    """
    Retorna:
        (output: str, is_success: bool)
    Levanta:
        TransientInfraError: Se houver timeout ou instabilidade.
        FatalInfraError: Se a sandbox expirou ou der erro grave.
    """
    logger.info(f"🏃 RUNNER: Executando testes na Sandbox {sandbox_id[:8]}...")
    
    try:
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)
        
        # Garante o pytest
        sandbox.commands.run(
            "python -c 'import pytest' 2>/dev/null || pip install pytest -q",
            timeout=_PYTEST_TIMEOUT, request_timeout=_REQUEST_TIMEOUT
        )

        cmd = f'PYTHONPATH=. python -m pytest "{test_path}" {_PYTEST_FLAGS}'
        result = sandbox.commands.run(
            cmd, timeout=_PYTEST_TIMEOUT, request_timeout=_REQUEST_TIMEOUT
        )
        
        logger.info("✅ RUNNER: Todos os testes passaram.")
        return result.stdout or "Testes aprovados sem output", True

    except SandboxException as exc:
        # 1. Verifica se é o erro esperado do TDD (exit_code != 0)
        if type(exc).__name__ == "CommandExitException":
            output = f"--- STDOUT ---\n{getattr(exc, 'stdout', '')}\n--- STDERR ---\n{getattr(exc, 'stderr', '')}"
            
            if is_red_phase:
                logger.info("🔴 RUNNER: Testes falharam (Comportamento esperado no TDD).")
            else:
                logger.info("🔴 RUNNER: Testes falharam na validação.")
                
            return output, False
            
        # 2. Se for qualquer outro erro de sandbox, envia para o mapper classificar e disparar o erro
        handle_e2b_exception(exc, context="Runner")
        
    except Exception as exc:
        handle_e2b_exception(exc, context="Runner genérico")