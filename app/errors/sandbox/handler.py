"""
app/errors/e2b_error_handler.py

Classificação de exceções para chamadas ao E2B Sandbox SDK.

Hierarquia de decisão:

1. Tipo da exceção (mais confiável — via isinstance)
2. HTTP status code estruturado
3. Subtipo de TimeoutException via mensagem (workaround issue #463)
4. Palavras-chave na mensagem (último recurso)

TransientInfraError  -> erro recuperável (retry até 3x)
FatalInfraError      -> erro permanente (abortar fluxo)

NOTA: CommandExitException NÃO deve passar por aqui.
"""

from __future__ import annotations

import asyncio

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

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
# TimeoutException classification
# ---------------------------------------------------------------------------

_TIMEOUT_TRANSIENT_KEYWORDS = (
    "canceled",
    "deadline_exceeded",
    "execution timed out",
    "request timeout",
    "bad gateway",
)

_TIMEOUT_FATAL_KEYWORDS = (
    "unavailable",
    "sandbox timeout",
    "sandbox has expired",
)


# ---------------------------------------------------------------------------
# HTTP status codes
# ---------------------------------------------------------------------------

_TRANSIENT_HTTP_CODES = frozenset({
    408,
    409,
    425,
    429,
    500,
    502,
    503,
    504,
})

_FATAL_HTTP_CODES = frozenset({
    400,
    401,
    403,
    404,
    422,
})


# ---------------------------------------------------------------------------
# Keyword fallback (NO HTTP numbers)
# ---------------------------------------------------------------------------

_TRANSIENT_KEYWORDS = (
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "connection",
    "service unavailable",
    "overloaded",
)

_FATAL_KEYWORDS = (
    "invalid api key",
    "not found",
    "not enough space",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_status_code(exc: Exception) -> int | None:
    """Extrai HTTP status code de atributos estruturados."""
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status

    return None


def _raise_by_status(status_code: int, prefix: str, exc: Exception) -> None:
    """Classifica erro baseado em HTTP status."""
    if status_code in _TRANSIENT_HTTP_CODES:
        raise TransientInfraError(
            f"{prefix}Infraestrutura E2B instável (HTTP {status_code})."
        ) from exc

    if status_code in _FATAL_HTTP_CODES:
        raise FatalInfraError(
            f"{prefix}Erro permanente na requisição E2B (HTTP {status_code})."
        ) from exc

    raise FatalInfraError(
        f"{prefix}Erro HTTP E2B não classificado (HTTP {status_code})."
    ) from exc


def _raise_by_keywords(error_msg: str, prefix: str, exc: Exception) -> None:
    """Fallback final via keywords."""
    if any(k in error_msg for k in _TRANSIENT_KEYWORDS):
        raise TransientInfraError(
            f"{prefix}Instabilidade inferida no E2B Sandbox."
        ) from exc

    if any(k in error_msg for k in _FATAL_KEYWORDS):
        raise FatalInfraError(
            f"{prefix}Erro permanente inferido na resposta do E2B."
        ) from exc

    raise FatalInfraError(
        f"{prefix}Erro desconhecido no E2B Sandbox."
    ) from exc


def _classify_timeout(exc: TimeoutException, prefix: str) -> None:
    """
    Classificação de TimeoutException.

    Ordem:
    1. status code (se disponível)
    2. mensagem (workaround issue #463)
    """

    status_code = _extract_status_code(exc)

    if status_code is not None:
        _raise_by_status(status_code, prefix, exc)

    error_msg = str(exc).lower()

    if any(k in error_msg for k in _TIMEOUT_TRANSIENT_KEYWORDS):
        raise TransientInfraError(
            f"{prefix}Timeout recuperável no E2B (request/execução)."
        ) from exc

    if any(k in error_msg for k in _TIMEOUT_FATAL_KEYWORDS):
        raise FatalInfraError(
            f"{prefix}Sandbox E2B expirou ou está indisponível — recrie a sandbox."
        ) from exc

    raise FatalInfraError(
        f"{prefix}TimeoutException E2B não classificado."
    ) from exc


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
            _classify_timeout(exc, prefix)

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

        if isinstance(exc, SandboxException):
            status_code = _extract_status_code(exc)

            if status_code is not None:
                _raise_by_status(status_code, prefix, exc)

            _raise_by_keywords(error_msg, prefix, exc)

    # ------------------------------------------------------------------
    # 2. Network errors
    # ------------------------------------------------------------------

    if isinstance(exc, asyncio.TimeoutError):
        raise TransientInfraError(
            f"{prefix}Timeout de rede ao acessar E2B."
        ) from exc

    if _HTTPX_AVAILABLE and isinstance(
        exc,
        (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    ):
        raise TransientInfraError(
            f"{prefix}Erro de rede ao comunicar com E2B."
        ) from exc

    # ------------------------------------------------------------------
    # 3. HTTP fallback
    # ------------------------------------------------------------------

    status_code = _extract_status_code(exc)

    if status_code is not None:
        _raise_by_status(status_code, prefix, exc)

    # ------------------------------------------------------------------
    # 4. Final fallback
    # ------------------------------------------------------------------

    _raise_by_keywords(error_msg, prefix, exc)