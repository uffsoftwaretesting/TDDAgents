
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




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

def handle_e2b_exception(exc: Exception, context: str = "") -> None:
    """
    Classifica exceções do E2B SDK.

    Raises:
        TransientInfraError
        FatalInfraError
    """

    prefix = f"[{context}] " if context else ""
    error_msg = str(exc).lower()

    # ------------------------------------------------------------------
    # 1. E2B exception types
    # ------------------------------------------------------------------

    if _E2B_AVAILABLE:

        if isinstance(exc, TimeoutException):
            raise TransientInfraError(
                f"{prefix}Rate limit do E2B atingido."
            ) from exc

        if isinstance(exc, RateLimitException):
            raise TransientInfraError(
                f"{prefix}Rate limit do E2B atingido."
            ) from exc
    
        if isinstance(exc, SandboxException):
            raise TransientInfraError(
                f"{prefix}Sandbox error."
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
    raise FatalInfraError(
        f"{prefix}Falha não classificada de LLM: {exc}"
    ) from exc