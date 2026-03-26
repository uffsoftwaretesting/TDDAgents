from __future__ import annotations

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

try:
    from langchain_core.exceptions import OutputParserException
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False

try:
    from langgraph.errors import GraphRecursionError
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

from app.errors.exceptions import FatalInfraError, TransientInfraError


def _extract_openai_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status
    return getattr(exc, "http_status", None)


def _extract_openai_error_code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                return code
    return None


def handle_llm_exception(exc: Exception, context: str = "") -> None:
    """
    Classifica exceções de LLM como TransientInfraError ou FatalInfraError.
    """
    prefix = f"[{context}] " if context else ""

    # LangGraph
    if _LANGGRAPH_AVAILABLE:
        if isinstance(exc, GraphRecursionError):
            raise FatalInfraError(
                f"{prefix}Limite de recursão do grafo atingido."
            ) from exc

    # LangChain
    if _LANGCHAIN_AVAILABLE:
        if isinstance(exc, OutputParserException):
            raise FatalInfraError(
                f"{prefix}Resposta do LLM não pode ser parseada."
            ) from exc

    # OpenAI SDK
    if _OPENAI_AVAILABLE:
        # Específicas primeiro (RateLimitError herda APIStatusError)
        if isinstance(exc, RateLimitError):
            err_code = _extract_openai_error_code(exc)
            if err_code == "insufficient_quota":
                raise FatalInfraError(
                    f"{prefix}Quota insuficiente na API do provedor."
                ) from exc

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
            status = _extract_openai_status(exc)

            if status in (408, 409, 425, 429) or (isinstance(status, int) and 500 <= status <= 599):
                raise TransientInfraError(
                    f"{prefix}Erro transitório de status {status} da API do OpenAI."
                ) from exc

            raise FatalInfraError(
                f"{prefix}Erro permanente de status {status} da API do OpenAI."
            ) from exc

    raise FatalInfraError(
        f"{prefix}Falha não classificada de LLM: {exc}"
    ) from exc