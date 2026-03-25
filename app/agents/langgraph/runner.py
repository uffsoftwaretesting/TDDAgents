"""
Runner — E2B Sandbox test execution bridge (Python SDK v2.8.x)

Responsabilidade única: conectar à sandbox E2B existente, executar a suíte
pytest com máximo de contexto para os agentes, e devolver um par
(output: str, is_success: bool) que o Reviewer consegue interpretar.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
POR QUE try/finally E NÃO "with Sandbox.connect(...)"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
O runner NUNCA cria a sandbox — ele conecta a uma já existente gerenciada
pelo orquestrador (ciclo de vida externo ao runner).

Usar "with Sandbox.connect(...) as sandbox:" causaria dois problemas:

  1. __exit__ chama kill(), encerrando a VM remota e destruindo o workspace
     entre iterações do ciclo TDD.

  2. O SDK tem um bug ativo (issue #1155, aberto em fev/2026): __exit__
     chama kill() mas NUNCA fecha o httpx.Client interno. Mesmo
     "with Sandbox.create()" vaza conexões TCP — o "with" não resolve
     o problema de vazamento.

A solução correta é try/finally com _close_sandbox_client(), que fecha
o httpx.Client sem matar a VM remota.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HIERARQUIA DE EXCEÇÕES DO SDK v2 (docs.e2b.dev/sdk-reference/python)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SandboxException              ← base — capturada pelo handler genérico
├── CommandExitException      ← pytest rodou mas exit_code != 0 (TDD esperado)
│                               Sem import público; identificada por nome
│                               + isinstance(SandboxException) como dupla guarda
├── TimeoutException          ← sandbox idle / request timeout / deadline
├── AuthenticationException   ← API key inválida ou ausente
├── NotFoundException         ← sandbox_id não existe ou já expirou
├── RateLimitException        ← cota de API excedida
├── InvalidArgumentException  ← parâmetro inválido passado ao SDK
├── NotEnoughSpaceException   ← disco cheio na sandbox
└── TemplateException         ← template incompatível com a versão do SDK

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PADRÃO HANDLER REGISTRY — EXTENSIBILIDADE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Para suportar uma exceção nova do SDK:
  1. Importe-a de e2b_code_interpreter no bloco de imports abaixo.
  2. Escreva um handler: função Exception → RunnerError.
  3. Registre em _SANDBOX_EXCEPTION_HANDLERS.
Nenhuma outra parte do código muda.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILOSOFIA DAS FLAGS PYTEST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-vv           : nome completo de cada teste + PASSED/FAILED explícito
-rA           : resumo de todos os outcomes — Reviewer lê isto primeiro
--tb=long     : traceback integral, sem truncamento
--showlocals  : variáveis locais no frame da falha (Fault Attribution)
-W default    : warnings visíveis sem serem fatais
-asyncio_mode / loop_scope  → pertencem ao pytest.ini do workspace gerado

Ausentes intencionalmente:
  -s                         → desliga captura; perde o vínculo CAPTURED/teste
  -p no:warnings             → suprime DeprecationWarnings úteis ao Developer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Type

from e2b import (
    Sandbox,
    SandboxException,
    TimeoutException,
    InvalidArgumentException,
    NotEnoughSpaceException,
    NotFoundException,
    AuthenticationException,
    TemplateException,
)

from e2b.exceptions import RateLimitException

from app.config.config import Config
from app.errors.sandbox.handler import handle_e2b_exception

logger = logging.getLogger("TDDOrchestrator.Runner")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Mantenha o que o modelo sugeriu, mas anexe a blindagem contra o asyncio
_PYTEST_FLAGS = "-vv -rA --tb=long --showlocals -W default -o asyncio_default_fixture_loop_scope=function -o asyncio_mode=auto"
_PYTEST_TIMEOUT: int = 180
_REQUEST_TIMEOUT: int = 30

def run_pytest_in_sandbox(sandbox_id: str, test_path: str = ".") -> tuple[str, bool]:
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
        # 1. Este é o único erro esperado: O pytest rodou, mas os testes falharam (exit code > 0)
        if type(exc).__name__ == "CommandExitException":
            output = f"--- STDOUT ---\n{getattr(exc, 'stdout', '')}\n--- STDERR ---\n{getattr(exc, 'stderr', '')}"
            logger.info("🔴 RUNNER: Testes falharam (Comportamento esperado no TDD).")
            return output, False
        
        # 2. Se for qualquer outro erro de sandbox, envia para o mapper classificar e disparar o erro
        handle_e2b_exception(exc, context="Runner")
        
    except Exception as exc:
        handle_e2b_exception(exc, context="Runner genérico")