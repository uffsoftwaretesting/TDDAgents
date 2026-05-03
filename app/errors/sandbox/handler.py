from __future__ import annotations

try:
    from e2b import (
        AuthenticationException,
        NotFoundException,
        NotEnoughSpaceException,
        SandboxException,
        TemplateException,
        TimeoutException,
    )
    from e2b.exceptions import RateLimitException
    _E2B_AVAILABLE = True
except ImportError:
    _E2B_AVAILABLE = False

from app.errors.exceptions import FatalInfraError, TransientInfraError

def handle_e2b_exception(exc: Exception, context: str = "") -> None:
    """
    Classifica exceções do E2B SDK.

    Raises:
        TransientInfraError
        FatalInfraError
    """
    prefix = f"[{context}] " if context else ""

    if _E2B_AVAILABLE:
        # Prevenção extra caso um erro de comando de terminal alcance o handler
        if type(exc).__name__ == "CommandExitException":
            raise TransientInfraError(
                f"{prefix}Falha na execução de comando interno na Sandbox: {exc}"
            ) from exc

        if isinstance(exc, TimeoutException):
            raise TransientInfraError(
                f"{prefix}Timeout atingido no E2B."
            ) from exc

        if isinstance(exc, RateLimitException):
            raise TransientInfraError(
                f"{prefix}Rate limit do E2B atingido."
            ) from exc

        if isinstance(exc, AuthenticationException):
            raise FatalInfraError(
                f"{prefix}Falha de autenticação no E2B."
            ) from exc

        if isinstance(exc, NotFoundException):
            raise FatalInfraError(
                f"{prefix}Sandbox ou recurso não encontrado no E2B."
            ) from exc

        if isinstance(exc, NotEnoughSpaceException):
            raise FatalInfraError(
                f"{prefix}Espaço em disco insuficiente na sandbox."
            ) from exc

        if isinstance(exc, TemplateException):
            raise FatalInfraError(
                f"{prefix}Template da sandbox inválido."
            ) from exc
            
        # SandboxException atua como base para as exceções acima, então ela
        # deve vir por último para capturar quaisquer outros erros desconhecidos do E2B
        if isinstance(exc, SandboxException):
            raise TransientInfraError(
                f"{prefix}Sandbox error: {exc}"
            ) from exc

    raise FatalInfraError(
        f"{prefix}Falha não classificada de infraestrutura: {exc}"
    ) from exc