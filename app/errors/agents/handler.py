from __future__ import annotations

from typing import Never

# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
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
    # GraphBubbleUp is the base for all internal LangGraph control-flow
    # exceptions (GraphInterrupt, NodeInterrupt, ParentCommand).  These must
    # always be re-raised so the framework can handle them correctly.
    # NodeInterrupt is deprecated in LangGraph v1.0 in favour of interrupt(),
    # but we still guard it here for backward compatibility.
    from langgraph.errors import (
        EmptyChannelError,
        EmptyInputError,
        GraphBubbleUp,
        GraphRecursionError,
        InvalidUpdateError,
    )

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False

from app.errors.exceptions import FatalInfraError, TransientInfraError

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _openai_status(exc: Exception) -> int | None:
    """Extract the HTTP status code from an OpenAI SDK exception."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status
    return getattr(exc, "http_status", None)


def _openai_error_code(exc: Exception) -> str | None:
    """Extract the provider-level error code from the response body."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                return code
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def handle_llm_exception(exc: Exception, context: str = "") -> Never:
    """Classify an LLM-related exception as TransientInfraError or FatalInfraError.

    Call this inside an ``except`` block and let it always re-raise:

        try:
            result = llm.invoke(...)
        except Exception as exc:
            handle_llm_exception(exc, context="my_agent_node")

    Priority order (highest to lowest):
        1. LangGraph internal control-flow exceptions  →  re-raise as-is
        2. LangGraph graph-execution errors            →  Fatal
        3. LangChain output-parser errors              →  Transient or Fatal
        4. OpenAI SDK errors                           →  Transient or Fatal
        5. Unknown fallback                            →  Fatal

    LangChain / LangGraph errors are handled first because those libraries
    already normalise the underlying OpenAI errors; catching them earlier
    avoids duplicate handling.

    Args:
        exc: The exception that was caught.
        context: Optional free-form label (e.g. node name, chain ID) that is
            prepended to every error message for easier log correlation.

    Returns:
        Never — this function always raises.
    """
    prefix = f"[{context}] " if context else ""

    # ------------------------------------------------------------------
    # 1. LangGraph — internal control-flow bubbles (MUST be re-raised)
    #
    #    GraphBubbleUp is the base for GraphInterrupt, NodeInterrupt and
    #    ParentCommand.  These are *not* errors; they are the framework's
    #    mechanism for interrupts and cross-graph commands.  Wrapping them
    #    in our domain exceptions would break Human-in-the-Loop and
    #    subgraph routing entirely.
    # ------------------------------------------------------------------
    if _LANGGRAPH_AVAILABLE:
        if isinstance(exc, GraphBubbleUp):
            raise

    # ------------------------------------------------------------------
    # 2. LangGraph — graph-execution errors
    #
    #    GraphRecursionError: the graph exceeded recursion_limit.  This is
    #    almost always a design problem (infinite loop, missing stop
    #    condition) rather than an infra blip — treat as Fatal.
    #
    #    InvalidUpdateError: a node returned a value incompatible with the
    #    channel schema (wrong type, missing key, concurrent write without a
    #    reducer).  Again, this is a code/schema problem — Fatal.
    #
    #    EmptyInputError / EmptyChannelError: the caller passed None/empty
    #    where the graph requires input, or a node tried to read an
    #    uninitialised channel.  Both indicate misuse — Fatal.
    # ------------------------------------------------------------------
    if _LANGGRAPH_AVAILABLE:
        if isinstance(exc, GraphRecursionError):
            raise FatalInfraError(
                f"{prefix}LangGraph recursion limit reached. "
                "Check for infinite loops or increase recursion_limit."
            ) from exc

        if isinstance(exc, InvalidUpdateError):
            raise FatalInfraError(
                f"{prefix}LangGraph state update is invalid. "
                "A node returned a value incompatible with the channel schema."
            ) from exc

        if isinstance(exc, (EmptyInputError, EmptyChannelError)):
            raise FatalInfraError(
                f"{prefix}LangGraph received empty input or "
                "tried to read an uninitialised channel: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # 3. LangChain — output-parser errors
    #
    #    OutputParserException.send_to_llm == True means the parser itself
    #    considers the failure recoverable (it ships an observation + the
    #    raw LLM output back to the agent so it can self-correct).
    #    We therefore honour that signal and raise Transient.
    #
    #    send_to_llm == False means the output was structurally broken in a
    #    way the parser cannot advise the LLM about — treat as Fatal.
    # ------------------------------------------------------------------
    if _LANGCHAIN_AVAILABLE:
        if isinstance(exc, OutputParserException):
            if getattr(exc, "send_to_llm", False):
                raise TransientInfraError(
                    f"{prefix}LLM output could not be parsed; "
                    "the parser has requested a retry."
                ) from exc
            raise FatalInfraError(
                f"{prefix}LLM output could not be parsed and "
                "the parser did not supply retry context."
            ) from exc

    # ------------------------------------------------------------------
    # 4. OpenAI SDK errors
    #
    #    We check the most-specific subclasses first because several of them
    #    inherit from APIStatusError and would otherwise fall into the
    #    generic status-code branch below.
    #
    #    RateLimitError:
    #      - error_code == "insufficient_quota"  →  account out of credit (Fatal)
    #      - otherwise                           →  rate-limited, back off (Transient)
    #
    #    APITimeoutError / APIConnectionError:
    #      Network-level failures; always Transient.
    #
    #    InternalServerError (5xx from OpenAI):
    #      Transient — OpenAI infra blip.
    #
    #    AuthenticationError / PermissionDeniedError / NotFoundError:
    #      Configuration problems; retrying won't help — Fatal.
    #
    #    BadRequestError / UnprocessableEntityError:
    #      The request itself is malformed (e.g. prompt too long, invalid
    #      params) — Fatal.
    #
    #    Generic APIStatusError fallback:
    #      408 Request Timeout, 409 Conflict, 425 Too Early, 429 Too Many
    #      Requests, and the full 5xx range are transient.  Everything else
    #      is treated as Fatal.
    # ------------------------------------------------------------------
    if _OPENAI_AVAILABLE:
        if isinstance(exc, RateLimitError):
            if _openai_error_code(exc) == "insufficient_quota":
                raise FatalInfraError(
                    f"{prefix}OpenAI quota exhausted; top up the account."
                ) from exc
            raise TransientInfraError(
                f"{prefix}OpenAI rate limit reached; back off and retry."
            ) from exc

        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            raise TransientInfraError(
                f"{prefix}OpenAI connectivity failure; retry after a moment."
            ) from exc

        if isinstance(exc, InternalServerError):
            raise TransientInfraError(
                f"{prefix}OpenAI internal server error; retry after a moment."
            ) from exc

        if isinstance(exc, AuthenticationError):
            raise FatalInfraError(
                f"{prefix}OpenAI authentication failed; check API key."
            ) from exc

        if isinstance(exc, PermissionDeniedError):
            raise FatalInfraError(
                f"{prefix}OpenAI permission denied; check account access."
            ) from exc

        if isinstance(exc, NotFoundError):
            raise FatalInfraError(
                f"{prefix}OpenAI model or resource not found; "
                "check model name and availability."
            ) from exc

        if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
            raise FatalInfraError(
                f"{prefix}Malformed request sent to OpenAI "
                "(invalid params, prompt too long, etc.)."
            ) from exc

        # Generic APIStatusError — covers undocumented or future status codes
        # that were not matched by the more specific classes above.
        if isinstance(exc, APIStatusError):
            status = _openai_status(exc)
            _TRANSIENT_STATUS_CODES = {408, 409, 425, 429}
            if status in _TRANSIENT_STATUS_CODES or (
                isinstance(status, int) and 500 <= status <= 599
            ):
                raise TransientInfraError(
                    f"{prefix}OpenAI returned transient HTTP {status}; retry."
                ) from exc
            raise FatalInfraError(
                f"{prefix}OpenAI returned permanent HTTP {status}."
            ) from exc

    # ------------------------------------------------------------------
    # 5. Fallback — unknown exception type
    #    Include the type name for easier log triage.
    # ------------------------------------------------------------------
    raise FatalInfraError(
        f"{prefix}Unclassified LLM failure "
        f"({type(exc).__qualname__}): {exc}"
    ) from exc