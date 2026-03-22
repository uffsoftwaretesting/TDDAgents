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

from app.config import Config

logger = logging.getLogger("TDDOrchestrator.Runner")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Mantenha o que o modelo sugeriu, mas anexe a blindagem contra o asyncio
_PYTEST_FLAGS = "-vv -rA --tb=long --showlocals -W default -o asyncio_default_fixture_loop_scope=function -o asyncio_mode=auto"
_PYTEST_TIMEOUT: int = 180
_REQUEST_TIMEOUT: int = 30


# ---------------------------------------------------------------------------
# Tipo de dados interno
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunnerError:
    """
    Erro de infra capturado pelo runner.

    is_retryable permite que o orquestrador decida se repete a chamada
    (RateLimitException, REQUEST_TIMEOUT) ou precisa recriar a sandbox
    (NotFoundException, SANDBOX_IDLE_TIMEOUT) — sem parse de strings.
    """
    exc_type: str
    header: str
    detail: str
    stdout: str = ""
    stderr: str = ""
    is_retryable: bool = False


ExceptionHandler = Callable[[Exception], RunnerError]


# ---------------------------------------------------------------------------
# Gerenciamento de conexão
# ---------------------------------------------------------------------------

def _close_sandbox_client(sandbox: Sandbox) -> None:
    """
    Fecha o httpx.Client interno sem matar a VM remota.

    Workaround para o issue #1155 do E2B SDK (aberto fev/2026):
    __exit__ e kill() nunca fecham o httpx.Client, vazando conexões TCP
    a cada execução. close() é o método correto quando disponível.
    Em versões do SDK que ainda não têm close(), fechamos _envd_api
    diretamente como fallback defensivo.
    """
    close_fn = getattr(sandbox, "close", None)
    if callable(close_fn):
        try:
            close_fn()
            return
        except Exception as exc:
            logger.debug("sandbox.close() falhou, tentando fallback: %s", exc)

    client = getattr(sandbox, "_envd_api", None)
    if client is not None:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as exc:
                logger.debug("_envd_api.close() falhou: %s", exc)


# ---------------------------------------------------------------------------
# Helpers de extração de logs
# ---------------------------------------------------------------------------

def _extract_logs(exc: Exception) -> tuple[str, str]:
    """
    Extrai stdout/stderr do objeto de exceção do SDK v2.

    CommandExitException guarda os logs em exc.result (CommandResult).
    Fallback para atributos diretos, observado em algumas versões 2.x.
    """
    result = getattr(exc, "result", None)
    if result is not None:
        return (
            getattr(result, "stdout", "") or "",
            getattr(result, "stderr", "") or "",
        )
    return (
        getattr(exc, "stdout", "") or "",
        getattr(exc, "stderr", "") or "",
    )


def _classify_timeout(exc: TimeoutException) -> tuple[str, bool]:
    """
    Infere subtipo e retryability de TimeoutException pela mensagem.

    Workaround para o issue #463 do E2B — TimeoutException não expõe
    campo .type na API pública do SDK v2. Retorna (descrição, is_retryable).
    """
    msg = str(exc).lower()
    if "502" in msg or "bad gateway" in msg or "sandbox timeout" in msg:
        return "SANDBOX_IDLE_TIMEOUT — sandbox expirou por inatividade.", False
    if "deadline_exceeded" in msg or "execution timed out" in msg:
        return f"EXECUTION_TIMEOUT — pytest ultrapassou {_PYTEST_TIMEOUT}s.", False
    if "canceled" in msg or "request timeout" in msg:
        return "REQUEST_TIMEOUT — timeout na chamada de API ao E2B.", True
    return "TIMEOUT_UNKNOWN — subtipo não identificado.", False


# ---------------------------------------------------------------------------
# Helpers de leitura de output pytest
# ---------------------------------------------------------------------------

def _count_pytest_summary(stdout: str) -> dict[str, int]:
    """
    Extrai contagens do resumo final do pytest a partir do stdout.

    Procura pela linha de resumo do pytest, exemplos:
      "1 passed"  /  "2 failed, 1 passed"  /  "3 error"
    Retorna dict com chaves: passed, failed, error, warning, skipped, xfailed.
    Usado pelo runner para distinguir "nenhum teste coletado" de
    "testes falharam" — ambos produzem CommandExitException, mas têm
    causas e rotas de correção completamente diferentes.
    """
    import re
    counts: dict[str, int] = {
        "passed": 0, "failed": 0, "error": 0,
        "warning": 0, "skipped": 0, "xfailed": 0,
    }
    # A linha de resumo do pytest começa com "==" e contém números + labels
    # Ex: "====== 2 failed, 1 passed in 0.12s ======"
    for line in reversed(stdout.splitlines()):
        if line.startswith("=") and ("passed" in line or "failed" in line
                                      or "error" in line or "no tests ran" in line):
            for key in counts:
                m = re.search(rf"(\d+)\s+{key}", line)
                if m:
                    counts[key] = int(m.group(1))
            break
    return counts


def _summarise_pytest_failure(stdout: str, stderr: str) -> str:
    """
    Produz uma linha de resumo legível para o log do orquestrador,
    complementando o output completo que vai ao Reviewer.

    Exemplos de saída:
      "2 failed, 1 passed"
      "3 error (coleta/importação)"
      "nenhum teste coletado — verifique test_path e imports"
    """
    counts = _count_pytest_summary(stdout)

    if counts["error"] > 0 and counts["passed"] == 0 and counts["failed"] == 0:
        return f"{counts['error']} error (coleta/importação)"

    parts = []
    for label in ("failed", "error", "passed", "skipped", "xfailed"):
        if counts[label]:
            parts.append(f"{counts[label]} {label}")

    if parts:
        return ", ".join(parts)

    # Nenhuma linha de resumo encontrada — indica ausência de testes coletados
    # ou falha anterior ao início da execução (ImportError no conftest, etc.)
    if "no tests ran" in stdout or "collected 0 items" in stdout:
        return "nenhum teste coletado — verifique test_path e imports"

    return "output inesperado — sem linha de resumo do pytest"


# ---------------------------------------------------------------------------
# Handlers individuais
# ---------------------------------------------------------------------------

def _handle_command_exit(exc: Exception) -> RunnerError:
    """
    Caso esperado no TDD: pytest rodou mas exit_code != 0.
    Não é erro de infra. Header vazio: o output do pytest é autoexplicativo
    para o Reviewer. O log do orquestrador usa o summary para ser legível.
    """
    stdout, stderr = _extract_logs(exc)
    return RunnerError(
        exc_type="CommandExitException",
        header="",
        detail="",
        stdout=stdout,
        stderr=stderr,
        is_retryable=False,
    )


def _handle_timeout(exc: Exception) -> RunnerError:
    description, retryable = _classify_timeout(exc)  # type: ignore[arg-type]
    stdout, stderr = _extract_logs(exc)
    return RunnerError(
        exc_type="TimeoutException",
        header=f"TIMEOUT — {description}",
        detail=(
            "Verificar se há loops infinitos ou imports pesados nos testes. "
            "Se for SANDBOX_IDLE_TIMEOUT, o orquestrador deve recriar a "
            f"sandbox.\nDetalhe original: {exc}"
        ),
        stdout=stdout,
        stderr=stderr,
        is_retryable=retryable,
    )


def _handle_authentication(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="AuthenticationException",
        header="AUTENTICAÇÃO FALHOU — API key inválida ou ausente.",
        detail=(
            "Verifique Config.E2B_API_KEY. Não é causado pelo código dos "
            f"agentes.\nDetalhe original: {exc}"
        ),
        is_retryable=False,
    )


def _handle_not_found(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="NotFoundException",
        header="SANDBOX NÃO ENCONTRADA — sandbox_id inexistente ou expirado.",
        detail=(
            "O orquestrador deve recriar a sandbox e ressincronizar o "
            f"workspace.\nDetalhe original: {exc}"
        ),
        is_retryable=False,
    )


def _handle_rate_limit(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="RateLimitException",
        header="RATE LIMIT — cota de API do E2B excedida.",
        detail=(
            "O orquestrador deve aguardar antes de tentar novamente. "
            f"Detalhe original: {exc}"
        ),
        is_retryable=True,
    )


def _handle_invalid_argument(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="InvalidArgumentException",
        header="ARGUMENTO INVÁLIDO — parâmetro incorreto passado ao SDK.",
        detail=(
            "Provável bug no runner ou nos parâmetros de chamada. "
            f"Detalhe original: {exc}"
        ),
        is_retryable=False,
    )


def _handle_not_enough_space(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="NotEnoughSpaceException",
        header="DISCO CHEIO — sem espaço na sandbox.",
        detail=(
            "O Developer pode estar gerando artefatos muito grandes. "
            "Recriar a sandbox ou limpar o workspace. "
            f"Detalhe original: {exc}"
        ),
        is_retryable=False,
    )


def _handle_template(exc: Exception) -> RunnerError:
    return RunnerError(
        exc_type="TemplateException",
        header="TEMPLATE INCOMPATÍVEL — envd desatualizado.",
        detail=(
            "O template da sandbox é incompatível com esta versão do SDK. "
            f"Reconstrua o template E2B.\nDetalhe original: {exc}"
        ),
        is_retryable=False,
    )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_SANDBOX_EXCEPTION_HANDLERS: dict[Type[SandboxException], ExceptionHandler] = {
    TimeoutException:           _handle_timeout,
    AuthenticationException:    _handle_authentication,
    NotFoundException:          _handle_not_found,
    RateLimitException:         _handle_rate_limit,
    InvalidArgumentException:   _handle_invalid_argument,
    NotEnoughSpaceException:    _handle_not_enough_space,
    TemplateException:          _handle_template,
}


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------

def _dispatch(exc: Exception) -> tuple[RunnerError, str]:
    """
    Mapeia uma exceção para (RunnerError, log_level).

    Ordem de despacho:
      1. CommandExitException — dupla guarda: isinstance(SandboxException)
         + nome. Se o SDK renomear a classe, o isinstance captura e o
         fallback genérico emite aviso explícito em vez de silenciar.
      2. Subclasses específicas no registry.
      3. SandboxException genérica (sem handler registrado).
      4. Exception fora da hierarquia do SDK (bug no runner).
    """
    exc_name = type(exc).__name__

    # 1. Caso esperado do TDD — WARNING, não ERROR
    if isinstance(exc, SandboxException) and exc_name == "CommandExitException":
        return _handle_command_exit(exc), "warning"

    # 2. Subclasses específicas registradas
    for exc_type, handler in _SANDBOX_EXCEPTION_HANDLERS.items():
        if isinstance(exc, exc_type):
            return handler(exc), "error"

    # 3. SandboxException genérica não coberta pelo registry
    if isinstance(exc, SandboxException):
        stdout, stderr = _extract_logs(exc)
        error = RunnerError(
            exc_type=exc_name,
            header=f"INFRAESTRUTURA E2B — {exc_name} (sem handler específico).",
            detail=(
                "Exceção de infra não catalogada. Verifique se o SDK foi "
                "atualizado e adicione um handler em _SANDBOX_EXCEPTION_HANDLERS "
                f"se necessário.\nDetalhe original: {exc}"
            ),
            stdout=stdout,
            stderr=stderr,
            is_retryable=False,
        )
        return error, "error"

    # 4. Fora da hierarquia do SDK — bug no runner
    stdout, stderr = _extract_logs(exc)
    error = RunnerError(
        exc_type=exc_name,
        header=f"ERRO INTERNO DO RUNNER — {exc_name}.",
        detail=(
            "Exceção fora da hierarquia do SDK E2B. Verifique os logs do "
            f"orquestrador para o traceback completo.\nDetalhe original: {exc}"
        ),
        stdout=stdout,
        stderr=stderr,
        is_retryable=False,
    )
    return error, "critical"


# ---------------------------------------------------------------------------
# Formatação de output
# ---------------------------------------------------------------------------

def _build_output(error: RunnerError) -> str:
    """
    Serializa RunnerError para a string consumida pelo Reviewer.
    Seções rotuladas — nenhuma informação silenciada.
    """
    sections: list[str] = []
    if error.header:
        sections.append(f"{'=' * 60}\n{error.header}\n{'=' * 60}")
    if error.detail:
        sections.append(error.detail)
    if error.stdout.strip():
        sections.append(f"--- STDOUT ---\n{error.stdout.strip()}")
    if error.stderr.strip():
        sections.append(f"--- STDERR ---\n{error.stderr.strip()}")
    return "\n\n".join(sections) if sections else "AVISO: Nenhum output retornado."


def _build_success_output(result: object) -> str:
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    sections = []
    if stdout.strip():
        sections.append(f"--- STDOUT ---\n{stdout.strip()}")
    if stderr.strip():
        sections.append(f"--- STDERR ---\n{stderr.strip()}")
    return "\n\n".join(sections) if sections else "AVISO: Nenhum output retornado."


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def run_pytest_in_sandbox(
    sandbox_id: str,
    test_path: str = ".",
) -> tuple[str, bool]:
    """
    Conecta à Sandbox E2B ativa e executa o pytest.

    Parâmetros
    ----------
    sandbox_id : str
        ID da sandbox E2B previamente criada e gerenciada pelo orquestrador.
        O runner NUNCA encerra a sandbox — apenas usa e libera a conexão.
    test_path : str
        Caminho relativo (dentro da sandbox) onde o pytest deve rodar.

    Retorno
    -------
    output : str
        Saída consolidada (stdout + stderr + diagnóstico). Sempre não-vazio.
    is_success : bool
        True  → todos os testes passaram (exit_code == 0)
        False → falha de teste, erro de importação ou falha de infra
    """
    logger.info("🏃 RUNNER: Executando testes na Sandbox %s...", sandbox_id[:8])

    sandbox: Sandbox | None = None
    try:
        # 1. Conexão — sem "with": __exit__ mataria a VM remota (ver docstring)
        sandbox = Sandbox.connect(sandbox_id, api_key=Config.E2B_API_KEY)

        # 2. Garantir pytest disponível (pip é no-op se já instalado)
        sandbox.commands.run(
            "python -c 'import pytest' 2>/dev/null || pip install pytest -q",
            timeout=_PYTEST_TIMEOUT,
            request_timeout=_REQUEST_TIMEOUT,
        )

        # 3. Executar pytest
        #    exit_code == 0 → retorna CommandResult
        #    exit_code != 0 → SDK lança CommandExitException
        cmd = f'PYTHONPATH=. python -m pytest "{test_path}" {_PYTEST_FLAGS}'
        result = sandbox.commands.run(
            cmd,
            timeout=_PYTEST_TIMEOUT,
            request_timeout=_REQUEST_TIMEOUT,
        )

        logger.info("✅ RUNNER: Todos os testes passaram.")
        return _build_success_output(result), True

    except Exception as exc:
        error, log_level = _dispatch(exc)

        _LOG_FN = {
            "warning":  logger.warning,
            "error":    logger.error,
            "critical": logger.critical,
        }
        _ICON = {"warning": "🔴", "error": "❌", "critical": "💥"}

        # BUG CORRIGIDO: CommandExitException tem header="" e detail="" porque
        # o output do pytest é suficiente para o Reviewer. Porém o log do
        # orquestrador ficava em branco ("🔴 RUNNER [CommandExitException]: ").
        # Agora extraímos um resumo legível diretamente do stdout do pytest,
        # separando "2 failed, 1 passed" de "3 error (coleta/importação)" de
        # "nenhum teste coletado" — três causas com rotas de correção distintas.
        if log_level == "warning" and not error.header:
            log_summary = _summarise_pytest_failure(error.stdout, error.stderr)
        else:
            log_summary = error.header or error.detail

        _LOG_FN.get(log_level, logger.error)(
            "%s RUNNER [%s]: %s",
            _ICON.get(log_level, "❌"),
            error.exc_type,
            log_summary,
            exc_info=(log_level == "critical"),
        )

        return _build_output(error), False