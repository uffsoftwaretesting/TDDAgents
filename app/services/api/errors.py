"""
A failed model call as the assistant message the user reads.

Ported from `reference/claude-code/src/services/api/errors.ts`: `API_ERROR_MESSAGE_PREFIX`,
`startsWithApiErrorPrefix`, `PROMPT_TOO_LONG_ERROR_MESSAGE`, `CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE`,
`INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL`, `API_TIMEOUT_ERROR_MESSAGE`, the PDF / image / request-size texts
and `getAssistantMessageFromError` (l.425-933); `services/api/errorUtils.ts` -> `formatAPIError` and
`extractConnectionErrorDetails`.

`stream_call_model` (`claude.ts` `queryModel`) yields this message instead of raising, so the turn ends on
an API error the loop recognises (`is_api_error_message`) and the user sees why ("Credit balance is too
low", "Request timed out", "API Error: 429 …").

Ported branches are the ones the Anthropic and OpenAI SDKs TDDAgents calls can reach: timeout, 429, prompt
too long, PDF / image / request size, the tool_use pairing 400s, credit balance, an invalid API key, 401/403,
404, connection errors and the generic `API Error: <message>`. Not ported, because TDDAgents has none of
their paths: the claude.ai subscription rate-limit headers, OAuth, CCR, Bedrock, the Opus off switch, the
afk-mode beta and the ant-only branches. Where upstream's interactive text names a command TDDAgents has not
(`/login`, `/rewind`, `/model`, `--model`, "double press esc"), its non-interactive text is used, and the
404 text drops the switch-command sentence. Python's SDKs raise transport errors (`httpx`) where Node's
wrap them, so an `httpx` timeout is a timeout and any other `httpx` transport error a connection error.
"""

from __future__ import annotations

import errno
import re
import ssl

from langchain_core.messages import AIMessage

from app.constants.api_limits import API_PDF_MAX_PAGES, PDF_TARGET_RAW_SIZE
from app.utils.messages import create_assistant_api_error_message
from app.utils.tool_result_storage import format_file_size

API_ERROR_MESSAGE_PREFIX = "API Error"
PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"
CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE = "Credit balance is too low"
INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL = "Invalid API key · Fix external API key"
API_TIMEOUT_ERROR_MESSAGE = "Request timed out"

PDF_TOO_LARGE_ERROR_MESSAGE = (
    f"PDF too large (max {API_PDF_MAX_PAGES} pages, {format_file_size(PDF_TARGET_RAW_SIZE)}). Try reading the "
    "file a different way (e.g., extract text with pdftotext).")
PDF_PASSWORD_PROTECTED_ERROR_MESSAGE = "PDF is password protected. Try using a CLI tool to extract or convert the PDF."
PDF_INVALID_ERROR_MESSAGE = "The PDF file was not valid. Try converting it to text first (e.g., pdftotext)."
IMAGE_TOO_LARGE_ERROR_MESSAGE = "Image was too large. Try resizing the image or using a different approach."
REQUEST_TOO_LARGE_ERROR_MESSAGE = (
    f"Request too large (max {format_file_size(PDF_TARGET_RAW_SIZE)}). Try with a smaller file.")
#: `getAssistantMessageFromError`'s many-image text (non-interactive).
MANY_IMAGE_ERROR_MESSAGE = (
    "An image in the conversation exceeds the dimension limit for many-image requests (2000px). "
    "Start a new session with fewer images.")

_PDF_PAGES = re.compile(r"maximum of \d+ PDF pages")
_INNER_MESSAGE = re.compile(r"""["']message["']\s*:\s*["']([^"']*)["']""")

#: `SSL_ERROR_CODES`' cases in `formatAPIError`, by the verification error Python's `ssl` reports.
_SSL_TEXTS: dict[int, str] = {
    10: "Unable to connect to API: SSL certificate has expired",                       # X509_V_ERR_CERT_HAS_EXPIRED
    9: "Unable to connect to API: SSL certificate is not yet valid",                    # X509_V_ERR_CERT_NOT_YET_VALID
    23: "Unable to connect to API: SSL certificate has been revoked",                   # X509_V_ERR_CERT_REVOKED
    18: ("Unable to connect to API: Self-signed certificate detected. Check your proxy or corporate SSL "
         "certificates"),                                                               # DEPTH_ZERO_SELF_SIGNED_CERT
    19: ("Unable to connect to API: Self-signed certificate detected. Check your proxy or corporate SSL "
         "certificates"),                                                               # SELF_SIGNED_CERT_IN_CHAIN
    62: "Unable to connect to API: SSL certificate hostname mismatch",                  # X509_V_ERR_HOSTNAME_MISMATCH
}
_SSL_VERIFY_FAILED = ("Unable to connect to API: SSL certificate verification failed. Check your proxy or corporate "
                      "SSL certificates")


def starts_with_api_error_prefix(text: str) -> bool:
    """`startsWithApiErrorPrefix`."""
    return text.startswith((API_ERROR_MESSAGE_PREFIX, f"Please run /login · {API_ERROR_MESSAGE_PREFIX}"))


def _status(error: BaseException) -> int | None:
    """`error.status` of the SDK's `APIError` (`status_code` in Python's SDKs)."""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(error, "status", None)
    return status if isinstance(status, int) else None


def _is_sdk_class(error: BaseException, name: str) -> bool:
    """`error instanceof <SDK>.<name>`, for the Anthropic and OpenAI SDKs alike (both name their classes so)."""
    return any(klass.__name__ == name and klass.__module__.split(".")[0] in ("anthropic", "openai")
               for klass in type(error).__mro__)


def _is_timeout(error: BaseException) -> bool:
    """`APIConnectionTimeoutError`, or an `APIConnectionError` whose message mentions a timeout."""
    import httpx

    if _is_sdk_class(error, "APITimeoutError") or isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return True
    return _is_connection_error(error) and "timeout" in str(error).lower()


def _is_connection_error(error: BaseException) -> bool:
    """`APIConnectionError`; a raw `httpx` transport error is what Node's SDK would have wrapped as one."""
    import httpx

    return _is_sdk_class(error, "APIConnectionError") or isinstance(error, httpx.TransportError)


def _connection_details(error: BaseException) -> tuple[str, int | None] | None:
    """
    `extractConnectionErrorDetails`: the first error with a code in the cause chain (five deep). Python's
    code is the `errno` name (`ECONNREFUSED`), or `SSL` with the certificate's verification code.
    """
    current: BaseException | None = error
    for _ in range(5):
        if current is None:
            return None
        if isinstance(current, ssl.SSLCertVerificationError):
            return "SSL", getattr(current, "verify_code", None)
        if isinstance(current, ssl.SSLError):
            return "SSL", None
        code = getattr(current, "errno", None)
        if isinstance(code, int) and code in errno.errorcode:
            return errno.errorcode[code], None
        nxt = current.__cause__ or current.__context__
        current = nxt if nxt is not current else None
    return None


def format_api_error(error: BaseException) -> str:
    """`formatAPIError` (`errorUtils.ts` l.200-…): a connection error, told by its cause."""
    details = _connection_details(error)
    if details is not None:
        code, verify_code = details
        if code == "ETIMEDOUT":
            return "Request timed out. Check your internet connection and proxy settings"
        if code == "SSL":
            if verify_code in _SSL_TEXTS:
                return _SSL_TEXTS[verify_code]
            if verify_code is not None:
                return _SSL_VERIFY_FAILED
            return "Unable to connect to API: SSL error (SSL)"
    message = str(error)
    if message == "Connection error." or not message:
        if details is not None:
            return f"Unable to connect to API ({details[0]})"
        if message == "Connection error.":
            return "Unable to connect to API. Check your internet connection"
        return f"API error (status {_status(error) or 'unknown'})"
    return message


def _rate_limit_detail(error: BaseException) -> str:
    """The 429's own words: the inner `error.message` of its body, else the message without its status."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str) and inner["message"]:
            return str(inner["message"])
    stripped = re.sub(r"^(Error code: )?429\s*(- )?", "", str(error))
    match = _INNER_MESSAGE.search(stripped)
    return match.group(1) if match else stripped


def get_assistant_message_from_error(error: BaseException, model: str) -> AIMessage:
    """
    `getAssistantMessageFromError` (`errors.ts` l.425-933), in upstream's order, for the branches the module
    docstring lists.
    """
    message = str(error)
    lower = message.lower()
    status = _status(error)

    if _is_timeout(error):
        return create_assistant_api_error_message(API_TIMEOUT_ERROR_MESSAGE, error="unknown")

    if status == 429:
        detail = _rate_limit_detail(error)
        return create_assistant_api_error_message(
            f"{API_ERROR_MESSAGE_PREFIX}: Request rejected (429) · "
            f"{detail or 'this may be a temporary capacity issue — check status.anthropic.com'}",
            error="rate_limit")

    if "prompt is too long" in lower:
        # The content stays generic; the raw text (with the token counts) is the details reactive compact reads.
        return create_assistant_api_error_message(
            PROMPT_TOO_LONG_ERROR_MESSAGE, error="invalid_request", error_details=message)

    if _PDF_PAGES.search(message):
        return create_assistant_api_error_message(
            PDF_TOO_LARGE_ERROR_MESSAGE, error="invalid_request", error_details=message)
    if "The PDF specified is password protected" in message:
        return create_assistant_api_error_message(PDF_PASSWORD_PROTECTED_ERROR_MESSAGE, error="invalid_request")
    if "The PDF specified was not valid" in message:
        return create_assistant_api_error_message(PDF_INVALID_ERROR_MESSAGE, error="invalid_request")
    if status == 400 and "image exceeds" in message and "maximum" in message:
        return create_assistant_api_error_message(
            IMAGE_TOO_LARGE_ERROR_MESSAGE, error="invalid_request", error_details=message)
    if status == 400 and "image dimensions exceed" in message and "many-image" in message:
        return create_assistant_api_error_message(
            MANY_IMAGE_ERROR_MESSAGE, error="invalid_request", error_details=message)
    if status == 413:
        return create_assistant_api_error_message(REQUEST_TOO_LARGE_ERROR_MESSAGE, error="invalid_request")

    if status == 400 and "`tool_use` ids were found without `tool_result` blocks immediately after" in message:
        return create_assistant_api_error_message(
            "API Error: 400 due to tool use concurrency issues.", error="invalid_request")
    if status == 400 and "`tool_use` ids must be unique" in message:
        return create_assistant_api_error_message(
            "API Error: 400 duplicate tool_use ID in conversation history.",
            error="invalid_request", error_details=message)

    if "Your credit balance is too low" in message:
        return create_assistant_api_error_message(CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE, error="billing_error")

    if "x-api-key" in lower:
        # TDDAgents' key always comes from the environment: upstream's external-source text.
        return create_assistant_api_error_message(
            INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL, error="authentication_failed")
    if status in (401, 403):
        return create_assistant_api_error_message(
            f"Failed to authenticate. {API_ERROR_MESSAGE_PREFIX}: {message}", error="authentication_failed")

    if status == 404:
        return create_assistant_api_error_message(
            f"There's an issue with the selected model ({model}). It may not exist or you may not have access "
            "to it.", error="invalid_request")

    if _is_connection_error(error):
        return create_assistant_api_error_message(
            f"{API_ERROR_MESSAGE_PREFIX}: {format_api_error(error)}", error="unknown")

    return create_assistant_api_error_message(f"{API_ERROR_MESSAGE_PREFIX}: {message}", error="unknown")


def unwrap_error(error: BaseException, model: str) -> tuple[BaseException, str]:
    """`queryModel`'s catch: a `CannotRetryError` stands for its `originalError` and its context's model."""
    from app.services.api.with_retry import CannotRetryError

    if isinstance(error, CannotRetryError) and error.original_error is not None:
        context = error.context
        return error.original_error, str(getattr(context, "model", None) or model)
    return error, model


__all__ = [
    "API_ERROR_MESSAGE_PREFIX",
    "API_TIMEOUT_ERROR_MESSAGE",
    "CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE",
    "IMAGE_TOO_LARGE_ERROR_MESSAGE",
    "INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL",
    "MANY_IMAGE_ERROR_MESSAGE",
    "PDF_INVALID_ERROR_MESSAGE",
    "PDF_PASSWORD_PROTECTED_ERROR_MESSAGE",
    "PDF_TOO_LARGE_ERROR_MESSAGE",
    "PROMPT_TOO_LONG_ERROR_MESSAGE",
    "REQUEST_TOO_LARGE_ERROR_MESSAGE",
    "format_api_error",
    "get_assistant_message_from_error",
    "starts_with_api_error_prefix",
    "unwrap_error",
]
