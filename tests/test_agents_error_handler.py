"""
`handle_llm_exception` — the LLM side of the retry taxonomy.

Five priority tiers, and the order between them is load-bearing:

    1. LangGraph control-flow bubbles  ->  re-raised untouched
    2. LangGraph execution errors      ->  Fatal
    3. LangChain parser errors         ->  Transient or Fatal, per the parser's own signal
    4. OpenAI SDK errors               ->  Transient or Fatal
    5. Anything else                   ->  Fatal

Tier 1 is the one that matters most and is the easiest to break: GraphBubbleUp is not an
error, it is how LangGraph implements interrupts and cross-graph commands. Wrapping one
in a domain exception would silently disable human-in-the-loop and subgraph routing.

Constructing OpenAI SDK exceptions directly is awkward — they want a live `httpx`
response — so the fixtures below build the minimum each class actually reads.
"""

from __future__ import annotations

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from langgraph.errors import (
    EmptyChannelError,
    EmptyInputError,
    GraphBubbleUp,
    GraphRecursionError,
    InvalidUpdateError,
)
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

from app.errors.agents.handler import (
    _openai_error_code,
    _openai_status,
    handle_llm_exception,
)
from app.errors.exceptions import FatalInfraError, TDDWorkflowError, TransientInfraError


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def api_error(cls, status: int, body: dict | None = None):
    """Builds an OpenAI APIStatusError subclass the way the SDK would."""
    response = httpx.Response(status_code=status, request=_request())
    return cls("boom", response=response, body=body)


def raise_through(exc: Exception, context: str = ""):
    """
    Calls the handler from inside a real `except` block.

    Tier 1 uses a bare `raise`, which needs an exception currently being handled — so
    every call has to go through here, exactly as production call sites do.
    """
    try:
        raise exc
    except Exception as caught:
        handle_llm_exception(caught, context=context)


# ── Tier 1: LangGraph control flow must pass through untouched ───────────────

def test_a_graph_bubble_is_re_raised_unchanged():
    """
    The single most important case in this module. GraphBubbleUp carries interrupts and
    ParentCommand; converting it into a TDDWorkflowError would break human-in-the-loop.
    """
    bubble = GraphBubbleUp()

    with pytest.raises(GraphBubbleUp) as raised:
        raise_through(bubble)

    assert raised.value is bubble


def test_a_graph_bubble_is_not_wrapped_in_a_domain_error():
    with pytest.raises(GraphBubbleUp):
        raise_through(GraphBubbleUp())


def test_a_graph_bubble_ignores_the_context_label():
    """No prefixing, no rewrapping — it must come out exactly as it went in."""
    with pytest.raises(GraphBubbleUp):
        raise_through(GraphBubbleUp(), context="planner")


# ── Tier 2: LangGraph execution errors are fatal ─────────────────────────────

def test_a_recursion_error_is_fatal():
    with pytest.raises(FatalInfraError) as raised:
        raise_through(GraphRecursionError("too deep"))
    assert str(raised.value) == (
        "LangGraph recursion limit reached. "
        "Check for infinite loops or increase recursion_limit."
    )


def test_an_invalid_update_is_fatal():
    with pytest.raises(FatalInfraError) as raised:
        raise_through(InvalidUpdateError("bad channel write"))
    assert str(raised.value) == (
        "LangGraph state update is invalid. "
        "A node returned a value incompatible with the channel schema."
    )


@pytest.mark.parametrize("exc_type", [EmptyInputError, EmptyChannelError])
def test_empty_input_and_channel_errors_are_fatal(exc_type):
    with pytest.raises(FatalInfraError, match="empty input"):
        raise_through(exc_type("nothing there"))


def test_the_empty_channel_message_interpolates_the_original_error():
    """
    Regression test. The continuation line of this message was a plain string rather
    than an f-string, so it rendered the literal text `{exc}` and threw away the only
    detail identifying which channel was empty.
    """
    with pytest.raises(FatalInfraError) as raised:
        raise_through(EmptyInputError("channel 'plan' was never written"))

    assert "channel 'plan' was never written" in str(raised.value)
    assert "{exc}" not in str(raised.value)


# ── Tier 3: parser errors honour the parser's own retry signal ───────────────

def test_a_parser_error_marked_retryable_is_transient():
    """`send_to_llm=True` means the parser packaged advice for the model; honour it."""
    exc = OutputParserException("bad json", send_to_llm=True, observation="o", llm_output="l")
    with pytest.raises(TransientInfraError) as raised:
        raise_through(exc)
    assert str(raised.value) == (
        "LLM output could not be parsed; the parser has requested a retry."
    )


def test_a_parser_error_without_retry_context_is_fatal():
    with pytest.raises(FatalInfraError) as raised:
        raise_through(OutputParserException("bad json"))
    assert str(raised.value) == (
        "LLM output could not be parsed and the parser did not supply retry context."
    )


def test_a_missing_send_to_llm_attribute_defaults_to_fatal():
    """
    The flag is read with getattr and a default. Defaulting to True would silently
    retry every unparseable output, including the structurally broken ones.
    """

    class BareParserError(OutputParserException):
        pass

    exc = BareParserError("bad json")
    del exc.send_to_llm
    with pytest.raises(FatalInfraError, match="did not supply retry context"):
        raise_through(exc)


# ── Tier 4: OpenAI SDK ───────────────────────────────────────────────────────

def test_a_rate_limit_is_transient():
    with pytest.raises(TransientInfraError, match="rate limit"):
        raise_through(api_error(RateLimitError, 429))


def test_an_exhausted_quota_is_fatal_despite_being_a_rate_limit():
    """
    Same exception class, opposite verdict. Retrying an empty account just burns the
    retry budget, so the provider error code overrides the HTTP status here.
    """
    exc = api_error(RateLimitError, 429, body={"code": "insufficient_quota"})
    with pytest.raises(FatalInfraError, match="quota exhausted"):
        raise_through(exc)


def test_a_quota_code_in_the_wrapped_body_form_is_also_fatal():
    exc = api_error(RateLimitError, 429, body={"error": {"code": "insufficient_quota"}})
    with pytest.raises(FatalInfraError, match="quota exhausted"):
        raise_through(exc)


def test_a_timeout_is_transient():
    with pytest.raises(TransientInfraError, match="connectivity failure"):
        raise_through(APITimeoutError(request=_request()))


def test_a_connection_error_is_transient():
    with pytest.raises(TransientInfraError, match="connectivity failure"):
        raise_through(APIConnectionError(request=_request()))


def test_an_internal_server_error_is_transient():
    with pytest.raises(TransientInfraError, match="internal server error"):
        raise_through(api_error(InternalServerError, 500))


@pytest.mark.parametrize(
    "cls,status,pattern",
    [
        (AuthenticationError, 401, "authentication failed"),
        (PermissionDeniedError, 403, "permission denied"),
        (NotFoundError, 404, "model or resource not found"),
        (BadRequestError, 400, "Malformed request"),
        (UnprocessableEntityError, 422, "Malformed request"),
    ],
    ids=["auth", "permission", "not-found", "bad-request", "unprocessable"],
)
def test_configuration_and_request_errors_are_fatal(cls, status, pattern):
    """Retrying any of these produces the identical failure; they are not blips."""
    with pytest.raises(FatalInfraError, match=pattern):
        raise_through(api_error(cls, status))


@pytest.mark.parametrize("status", [408, 409, 425, 429])
def test_the_transient_status_codes_are_transient(status):
    with pytest.raises(TransientInfraError, match=f"transient HTTP {status}"):
        raise_through(api_error(APIStatusError, status))


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_the_whole_5xx_range_is_transient(status):
    with pytest.raises(TransientInfraError, match=f"transient HTTP {status}"):
        raise_through(api_error(APIStatusError, status))


@pytest.mark.parametrize("status", [402, 405, 418, 451, 600])
def test_other_status_codes_are_fatal(status):
    with pytest.raises(FatalInfraError, match=f"permanent HTTP {status}"):
        raise_through(api_error(APIStatusError, status))


@pytest.mark.parametrize("status", [499, 600])
def test_the_5xx_range_boundaries_are_exclusive(status):
    """499 and 600 sit just outside the retryable band and must not be retried."""
    with pytest.raises(FatalInfraError):
        raise_through(api_error(APIStatusError, status))


# ── Tier 5: fallback ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [ValueError("nope"), RuntimeError("nope")], ids=type)
def test_an_unknown_exception_is_fatal(exc):
    with pytest.raises(FatalInfraError, match="Unclassified LLM failure"):
        raise_through(exc)


def test_the_fallback_message_names_the_exception_type():
    """The type name is what makes an unclassified failure triageable from a log."""
    with pytest.raises(FatalInfraError) as raised:
        raise_through(ValueError("mystery"))

    assert "ValueError" in str(raised.value)
    assert "mystery" in str(raised.value)


# ── Cross-cutting ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "exc",
    [
        GraphRecursionError("x"),
        OutputParserException("x"),
        ValueError("x"),
    ],
    ids=["langgraph", "langchain", "unknown"],
)
def test_the_context_label_is_prefixed(exc):
    with pytest.raises(TDDWorkflowError) as raised:
        raise_through(exc, context="tester")
    assert str(raised.value).startswith("[tester] ")


def test_an_absent_context_adds_no_prefix():
    with pytest.raises(FatalInfraError) as raised:
        raise_through(ValueError("x"))
    assert not str(raised.value).startswith("[")


def every_classified_exception():
    """
    One instance per branch that raises a domain error — every tier except the
    control-flow bubble, which is re-raised rather than wrapped.
    """
    return [
        pytest.param(GraphRecursionError("x"), id="recursion"),
        pytest.param(InvalidUpdateError("x"), id="invalid-update"),
        pytest.param(EmptyInputError("x"), id="empty-input"),
        pytest.param(EmptyChannelError("x"), id="empty-channel"),
        pytest.param(
            OutputParserException("x", send_to_llm=True, observation="o", llm_output="l"),
            id="parser-retryable",
        ),
        pytest.param(OutputParserException("x"), id="parser-fatal"),
        pytest.param(api_error(RateLimitError, 429), id="rate-limit"),
        pytest.param(
            api_error(RateLimitError, 429, body={"code": "insufficient_quota"}),
            id="quota",
        ),
        pytest.param(APITimeoutError(request=_request()), id="timeout"),
        pytest.param(APIConnectionError(request=_request()), id="connection"),
        pytest.param(api_error(InternalServerError, 500), id="server-error"),
        pytest.param(api_error(AuthenticationError, 401), id="auth"),
        pytest.param(api_error(PermissionDeniedError, 403), id="permission"),
        pytest.param(api_error(NotFoundError, 404), id="not-found"),
        pytest.param(api_error(BadRequestError, 400), id="bad-request"),
        pytest.param(api_error(UnprocessableEntityError, 422), id="unprocessable"),
        pytest.param(api_error(APIStatusError, 429), id="status-transient"),
        pytest.param(api_error(APIStatusError, 402), id="status-fatal"),
        pytest.param(ValueError("x"), id="unknown"),
    ]


@pytest.mark.parametrize("exc", every_classified_exception())
def test_the_original_exception_is_preserved(exc):
    """
    Every branch, not a sample of them. Nodes log `exc.original_exc` when reporting a
    failure, so a branch that drops it turns a diagnosable error into "something went
    wrong" — and it is exactly the kind of omission that only shows up in the one
    branch nobody exercised.
    """
    with pytest.raises(TDDWorkflowError) as raised:
        raise_through(exc)
    assert raised.value.original_exc is exc


@pytest.mark.parametrize("exc", every_classified_exception())
def test_the_exception_chain_is_preserved(exc):
    """`raise ... from exc` keeps the original traceback attached for debugging."""
    with pytest.raises(TDDWorkflowError) as raised:
        raise_through(exc)
    assert raised.value.__cause__ is exc


# ── Helper functions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "body,expected",
    [
        ({"code": "insufficient_quota"}, "insufficient_quota"),   # unwrapped
        ({"error": {"code": "rate_limit"}}, "rate_limit"),        # wrapped
        ({"code": 42}, None),                                     # non-string code
        ({"error": {"code": 42}}, None),
        ({"error": "not a dict"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_openai_error_code_extraction(body, expected):
    assert _openai_error_code(api_error(RateLimitError, 429, body=body)) == expected


def test_openai_status_reads_status_code():
    assert _openai_status(api_error(APIStatusError, 503)) == 503


def test_openai_status_falls_back_to_http_status():
    class Legacy(Exception):
        http_status = 429

    assert _openai_status(Legacy()) == 429


def test_openai_status_is_none_when_absent():
    assert _openai_status(ValueError("no status here")) is None


def test_openai_status_ignores_a_non_integer_status():
    """A string status would sail past the 5xx range check into the Fatal branch."""

    class Odd(Exception):
        status_code = "503"

    assert _openai_status(Odd()) is None
