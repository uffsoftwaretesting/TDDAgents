import json
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.main import app


def make_request():
    # Create a minimal ASGI scope for a HTTP GET
    scope = {"type": "http", "method": "GET", "path": "/"}
    return Request(scope)

@pytest.mark.asyncio
async def test_validation_error_handler_registered_and_returns_error_response():
    # Ensure the handler is registered
    assert RequestValidationError in app.exception_handlers, \
        "RequestValidationError handler not registered"
    handler = app.exception_handlers[RequestValidationError]

    # Simulate a validation error
    errors = [{"loc": ("body", "content"), "msg": "field required", "type": "value_error.missing"}]
    exc = RequestValidationError(errors)
    request = make_request()

    # Call the handler
    response = await handler(request, exc)
    # Validate response type and status code
    assert isinstance(response, JSONResponse), "Response is not JSONResponse"
    assert response.status_code == 422

    # Parse and validate payload
    payload = json.loads(response.body.decode("utf-8"))
    assert payload.get("success") is False
    assert "error" in payload and isinstance(payload["error"], dict)
    assert payload["error"].get("code") == 422
    # The message should match the exception string
    assert payload["error"].get("message") == str(exc)

@pytest.mark.asyncio
async def test_generic_exception_handler_registered_and_returns_error_response():
    # Ensure the generic Exception handler is registered
    assert Exception in app.exception_handlers, "Generic Exception handler not registered"
    handler = app.exception_handlers[Exception]

    # Simulate a generic exception
    exc = Exception("Something went wrong")
    request = make_request()

    # Call the handler
    response = await handler(request, exc)
    # Validate response type and status code
    assert isinstance(response, JSONResponse), "Response is not JSONResponse"
    assert response.status_code == 500

    # Parse and validate payload
    payload = json.loads(response.body.decode("utf-8"))
    assert payload.get("success") is False
    assert "error" in payload and isinstance(payload["error"], dict)
    assert payload["error"].get("code") == 500
    # The message should match the exception string
    assert payload["error"].get("message") == str(exc)
