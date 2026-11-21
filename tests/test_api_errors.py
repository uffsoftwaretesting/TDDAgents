"""
`app/services/api/errors.py`: a failed model call as upstream's assistant message
(`reference/claude-code/src/services/api/errors.ts` `getAssistantMessageFromError`, `utils/messages.ts`
`createAssistantAPIErrorMessage`), built from the real SDK exception classes.
"""

from __future__ import annotations

import errno
import logging
import ssl
from typing import Any

import anthropic
import httpx
import openai
import pytest

from app.constants.messages import NO_CONTENT_MESSAGE
from app.services.api.errors import (
    API_TIMEOUT_ERROR_MESSAGE,
    CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE,
    IMAGE_TOO_LARGE_ERROR_MESSAGE,
    INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL,
    MANY_IMAGE_ERROR_MESSAGE,
    PDF_INVALID_ERROR_MESSAGE,
    PDF_PASSWORD_PROTECTED_ERROR_MESSAGE,
    PDF_TOO_LARGE_ERROR_MESSAGE,
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    REQUEST_TOO_LARGE_ERROR_MESSAGE,
    format_api_error,
    get_assistant_message_from_error,
    starts_with_api_error_prefix,
    unwrap_error,
)
from app.services.api.with_retry import CannotRetryError, RetryContext
from app.utils.messages import create_assistant_api_error_message

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
MODEL = "claude-sonnet-5-5"


def status_error(status: int, message: str, body: Any = None, sdk: Any = anthropic) -> Exception:
    response = httpx.Response(status, request=REQUEST)
    error: Exception = sdk.APIStatusError(message, response=response, body=body)
    return error


def read(error: BaseException) -> tuple[str, dict[str, Any]]:
    message = get_assistant_message_from_error(error, MODEL)
    return str(message.content), dict(message.additional_kwargs)


# ── createAssistantAPIErrorMessage ─────────────────────────────────────────────

def test_an_api_error_message_is_flagged_and_never_empty():
    message = create_assistant_api_error_message("", error="unknown", error_details="raw")
    assert message.content == NO_CONTENT_MESSAGE == "(no content)"
    assert message.additional_kwargs == {"is_api_error_message": True, "api_error": "unknown", "error_details": "raw"}
    assert create_assistant_api_error_message("boom").additional_kwargs == {"is_api_error_message": True}


def test_the_prefix_check():
    assert starts_with_api_error_prefix("API Error: x")
    assert starts_with_api_error_prefix("Please run /login · API Error")
    assert not starts_with_api_error_prefix("Request timed out")


# ── getAssistantMessageFromError, branch by branch ─────────────────────────────

@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(REQUEST), openai.APITimeoutError(REQUEST),
    anthropic.APIConnectionError(message="Request timeout while reading", request=REQUEST),
    httpx.ReadTimeout(""), httpx.ConnectTimeout("slow"), TimeoutError(),
])
def test_a_timeout(error):
    assert read(error) == (API_TIMEOUT_ERROR_MESSAGE, {"is_api_error_message": True, "api_error": "unknown"})


def test_a_429_says_what_the_api_said():
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "Number of requests exceeded"}}
    assert read(status_error(429, "Error code: 429 - {...}", body)) == (
        "API Error: Request rejected (429) · Number of requests exceeded",
        {"is_api_error_message": True, "api_error": "rate_limit"})
    assert read(status_error(429, "429 {\"error\": {\"message\": \"slow down\"}}"))[0] == \
        "API Error: Request rejected (429) · slow down"
    assert read(status_error(429, ""))[0] == (
        "API Error: Request rejected (429) · this may be a temporary capacity issue — check status.anthropic.com")


def test_prompt_too_long_keeps_the_raw_text_as_details():
    raw = "prompt is too long: 210000 tokens > 200000 maximum"
    content, flags = read(status_error(400, raw))
    assert content == PROMPT_TOO_LONG_ERROR_MESSAGE
    assert flags == {"is_api_error_message": True, "api_error": "invalid_request", "error_details": raw}


@pytest.mark.parametrize(("message", "status", "expected"), [
    ("A maximum of 100 PDF pages may be provided.", 400, PDF_TOO_LARGE_ERROR_MESSAGE),
    ("The PDF specified is password protected.", 400, PDF_PASSWORD_PROTECTED_ERROR_MESSAGE),
    ("The PDF specified was not valid.", 400, PDF_INVALID_ERROR_MESSAGE),
    ("image exceeds 5 MB maximum: 5316852 bytes > 5242880 bytes", 400, IMAGE_TOO_LARGE_ERROR_MESSAGE),
    ("image dimensions exceed max allowed size for many-image requests: 2000 pixels", 400, MANY_IMAGE_ERROR_MESSAGE),
    ("request_too_large", 413, REQUEST_TOO_LARGE_ERROR_MESSAGE),
    ("`tool_use` ids were found without `tool_result` blocks immediately after: toolu_1", 400,
     "API Error: 400 due to tool use concurrency issues."),
    ("messages.1: `tool_use` ids must be unique", 400, "API Error: 400 duplicate tool_use ID in conversation history."),
])
def test_media_size_and_pairing_errors(message, status, expected):
    assert read(status_error(status, message))[0] == expected


def test_the_credit_balance():
    error = status_error(400, "Your credit balance is too low to access the Anthropic API.")
    assert read(error) == (CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE, {"is_api_error_message": True,
                                                                  "api_error": "billing_error"})


def test_an_invalid_key_and_other_authentication_errors():
    assert read(status_error(401, "invalid x-api-key")) == (
        INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL, {"is_api_error_message": True, "api_error": "authentication_failed"})
    assert read(status_error(403, "permission denied", sdk=openai))[0] == (
        "Failed to authenticate. API Error: permission denied")


def test_a_404_names_the_model():
    assert read(status_error(404, "model not found"))[0] == (
        f"There's an issue with the selected model ({MODEL}). It may not exist or you may not have access to it.")


def test_connection_errors_are_told_by_their_cause():
    refused = anthropic.APIConnectionError(request=REQUEST)
    refused.__cause__ = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    assert read(refused)[0] == "API Error: Unable to connect to API (ECONNREFUSED)"
    assert read(anthropic.APIConnectionError(request=REQUEST))[0] == (
        "API Error: Unable to connect to API. Check your internet connection")
    expired = anthropic.APIConnectionError(request=REQUEST)
    cert = ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")
    cert.verify_code = 10
    expired.__cause__ = cert
    assert read(expired)[0] == "API Error: Unable to connect to API: SSL certificate has expired"
    unknown_cert = ssl.SSLCertVerificationError("x")
    unknown_cert.verify_code = 20
    assert format_api_error(_caused(unknown_cert)).startswith("Unable to connect to API: SSL certificate verification")
    # A raw transport error mid-stream is what Node's SDK would have wrapped as a connection error.
    assert read(httpx.RemoteProtocolError("peer closed connection"))[0] == "API Error: peer closed connection"
    assert read(httpx.RemoteProtocolError(""))[0] == "API Error: API error (status unknown)"


def _caused(cause: BaseException) -> Exception:
    error = anthropic.APIConnectionError(request=REQUEST)
    error.__cause__ = cause
    return error


def test_anything_else_is_api_error_with_its_message():
    assert read(ValueError("Mock Error")) == ("API Error: Mock Error", {"is_api_error_message": True,
                                                                        "api_error": "unknown"})
    assert read(status_error(500, "Internal server error"))[0] == "API Error: Internal server error"
    assert read(RuntimeError(""))[0] == "API Error: "


def test_a_retry_error_stands_for_its_original_error_and_model():
    context = RetryContext(model="claude-fallback")
    original = status_error(400, "Your credit balance is too low")
    assert unwrap_error(CannotRetryError(original, context), MODEL) == (original, "claude-fallback")
    plain = ValueError("x")
    assert unwrap_error(plain, MODEL) == (plain, MODEL)
    assert unwrap_error(CannotRetryError(None, context), MODEL)[1] == MODEL


# ── stream_call_model: queryModel's catch ──────────────────────────────────────

def test_stream_call_model_yields_the_credit_message_and_logs_the_error(monkeypatch, caplog):
    from app.query.config import build_run_config
    from tests.test_loop_model import _collect, _state

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise status_error(400, "Your credit balance is too low to access the Anthropic API.")

    monkeypatch.setattr("app.services.api.claude.get_chat_model", boom)
    with caplog.at_level(logging.ERROR):
        (message,) = _collect(_state(), build_run_config(run_id="t"))
    assert message.content == CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE
    assert message.additional_kwargs["is_api_error_message"] is True
    record = next(r for r in caplog.records if "API request failed" in r.getMessage())
    assert "APIStatusError" in record.getMessage() and record.exc_info is not None


def test_stream_call_model_lets_the_fallback_signal_through(monkeypatch):
    from app.query.config import build_run_config
    from app.services.api.with_retry import FallbackTriggeredError
    from tests.test_loop_model import _collect, _state

    async def signal(*args: Any, **kwargs: Any) -> Any:
        raise FallbackTriggeredError("a", "b")

    monkeypatch.setattr("app.services.api.claude.with_retry", signal)
    with pytest.raises(FallbackTriggeredError):
        _collect(_state(), build_run_config(run_id="t"))


def test_a_cancelled_request_ends_quietly(monkeypatch):
    from app.query.config import build_run_config
    from tests.test_loop_model import _collect, _state

    state = _state()
    state.tool_context.cancel.cancel()

    async def cancelled(*args: Any, **kwargs: Any) -> Any:
        raise CannotRetryError(None, RetryContext(model=MODEL))

    monkeypatch.setattr("app.services.api.claude.with_retry", cancelled)
    assert _collect(state, build_run_config(run_id="t")) == []


# ── the loop: query.ts's catch, then the API-error guard ───────────────────────

def test_a_mid_stream_failure_shows_upstreams_message_and_logs_type_and_stack(caplog):
    from langchain_core.messages import AIMessage

    from app.query.transitions import Terminal, Terminated
    from tests.test_loop_engine import collect

    async def dies(state: Any, config: Any) -> Any:
        yield AIMessage(content="", tool_calls=[{"name": "Bash", "args": {"command": "ls"}, "id": "c1"}])
        raise httpx.ReadTimeout("")

    with caplog.at_level(logging.ERROR):
        events = collect(dies)
    repaired = [e for e in events if getattr(e, "tool_call_id", None) == "c1"]
    error = [e for e in events if getattr(e, "additional_kwargs", {}).get("is_api_error_message")]
    assert len(repaired) == 1 and [getattr(e, "content", None) for e in error] == [NO_CONTENT_MESSAGE]
    assert events[-1] == Terminated(reason=Terminal.MODEL_ERROR)
    record = next(r for r in caplog.records if r.getMessage().startswith("Model call failed"))
    assert record.getMessage() == "Model call failed: ReadTimeout: " and record.exc_info is not None


def test_an_api_error_message_ends_the_turn_without_the_stop_hooks():
    from app.query.transitions import Terminal, Terminated
    from tests.test_loop_engine import FakeModel, FakeStopHooks, collect, make_fake_deps

    stop_hooks = FakeStopHooks()
    message = create_assistant_api_error_message(CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE, error="billing_error")
    model = FakeModel([[message]])
    events = collect(model, deps=make_fake_deps(model, stop_hooks=stop_hooks))
    assert events[-1] == Terminated(reason=Terminal.MODEL_ERROR)
    assert stop_hooks.seen == []
