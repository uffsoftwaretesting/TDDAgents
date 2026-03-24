"""
app/errors/llm_error_handler.py

Classificação de exceções para chamadas LLM usadas em LangGraph / LangChain.

Hierarquia de decisão:

1. Tipo da exceção (mais confiável)
2. HTTP status code estruturado
3. Palavras-chave na mensagem (último recurso)

TransientInfraError  -> erro recuperável (retry até 3x)
FatalInfraError      -> erro permanente (abortar fluxo)
"""

from __future__ import annotations

import asyncio

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# ---------------------------------------------------------------------------
# OpenAI SDK >= 1.x
# ---------------------------------------------------------------------------

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        APIStatusError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnprocessableEntityError,
    )

    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


# ---------------------------------------------------------------------------
# LangChain
# ---------------------------------------------------------------------------

try:
    from langchain_core.exceptions import OutputParserException

    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False


# ---------------------------------------------------------------------------
# LangGraph
# ---------------------------------------------------------------------------

try:
    from langgraph.errors import GraphRecursionError, NodeInterrupt

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False


from app.errors.exceptions import FatalInfraError, TransientInfraError


# ---------------------------------------------------------------------------
# HTTP status classification
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
    529,
})

_FATAL_HTTP_CODES = frozenset({
    400,
    401,
    403,
    404,
    422,
})


# ---------------------------------------------------------------------------
# Fallback keywords
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
    "api key",
    "invalid api key",
    "authentication",
    "permission denied",
    "context length",
    "maximum context",
    "content filter",
    "model not found",
    "invalid model",
    "insufficient_quota",
    "billing",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_status_code(exc: Exception) -> int | None:
    """Extrai status HTTP de atributos estruturados."""
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
            f"{prefix}Infraestrutura instável (HTTP {status_code})."
        ) from exc

    if status_code in _FATAL_HTTP_CODES:
        raise FatalInfraError(
            f"{prefix}Erro permanente na requisição (HTTP {status_code})."
        ) from exc

    # default conservador
    raise FatalInfraError(
        f"{prefix}Erro HTTP não classificado (HTTP {status_code})."
    ) from exc


def _raise_by_keywords(error_msg: str, prefix: str, exc: Exception) -> None:
    """Fallback final por mensagem."""
    if any(k in error_msg for k in _TRANSIENT_KEYWORDS):
        raise TransientInfraError(
            f"{prefix}Instabilidade inferida do provedor LLM."
        ) from exc

    if any(k in error_msg for k in _FATAL_KEYWORDS):
        raise FatalInfraError(
            f"{prefix}Erro permanente inferido da resposta do LLM."
        ) from exc

    raise FatalInfraError(
        f"{prefix}Erro desconhecido durante chamada ao LLM."
    ) from exc


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle_llm_exception(exc: Exception, context: str = "") -> None:
    """
    Classifica exceções de LLM como TransientInfraError ou FatalInfraError.

    Uso típico:

        try:
            llm.invoke(...)
        except Exception as exc:
            handle_llm_exception(exc, context="agent_node")

    Retries devem ocorrer fora deste handler.
    """

    prefix = f"[{context}] " if context else ""
    error_msg = str(exc).lower()

    # ------------------------------------------------------------------
    # LangGraph
    # ------------------------------------------------------------------

    if _LANGGRAPH_AVAILABLE:

        if isinstance(exc, NodeInterrupt):
            raise

        if isinstance(exc, GraphRecursionError):
            raise FatalInfraError(
                f"{prefix}Limite de recursão do grafo atingido."
            ) from exc

    # ------------------------------------------------------------------
    # LangChain
    # ------------------------------------------------------------------

    if _LANGCHAIN_AVAILABLE:

        if isinstance(exc, OutputParserException):
            raise FatalInfraError(
                f"{prefix}Resposta do LLM não pode ser parseada."
            ) from exc

    # ------------------------------------------------------------------
    # OpenAI SDK
    # ------------------------------------------------------------------

    if _OPENAI_AVAILABLE:

        if isinstance(exc, RateLimitError):
            raise TransientInfraError(
                f"{prefix}Rate limit do provedor atingido."
            ) from exc

        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            raise TransientInfraError(
                f"{prefix}Falha de conectividade com API do provedor."
            ) from exc

        if isinstance(exc, InternalServerError):
            raise TransientInfraError(
                f"{prefix}Erro interno do provedor LLM."
            ) from exc

        if isinstance(exc, AuthenticationError):
            raise FatalInfraError(
                f"{prefix}Falha de autenticação da API."
            ) from exc

        if isinstance(exc, PermissionDeniedError):
            raise FatalInfraError(
                f"{prefix}Permissão negada pela API."
            ) from exc

        if isinstance(exc, NotFoundError):
            raise FatalInfraError(
                f"{prefix}Modelo ou recurso não encontrado."
            ) from exc

        if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
            raise FatalInfraError(
                f"{prefix}Requisição inválida enviada ao LLM."
            ) from exc

        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                _raise_by_status(status, prefix, exc)

    # ------------------------------------------------------------------
    # Network errors
    # ------------------------------------------------------------------

    if isinstance(exc, asyncio.TimeoutError):
        raise TransientInfraError(
            f"{prefix}Timeout de rede durante chamada ao LLM."
        ) from exc

    if _HTTPX_AVAILABLE:

        if isinstance(exc, (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        )):
            raise TransientInfraError(
                f"{prefix}Erro de rede ao comunicar com o provedor."
            ) from exc

    # ------------------------------------------------------------------
    # HTTP fallback
    # ------------------------------------------------------------------

    status_code = _extract_status_code(exc)

    if status_code is not None:
        _raise_by_status(status_code, prefix, exc)

    # ------------------------------------------------------------------
    # Final fallback
    # ------------------------------------------------------------------

    _raise_by_keywords(error_msg, prefix, exc)