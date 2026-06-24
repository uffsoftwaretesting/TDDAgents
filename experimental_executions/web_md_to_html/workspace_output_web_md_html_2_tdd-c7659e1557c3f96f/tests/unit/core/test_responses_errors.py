import pytest

from app.core.errors import ConversionError
from app.core.responses import success_response, error_response


def test_conversion_error_inherits_from_exception_and_stores_message():
    message = "Conversion failed due to X"
    err = ConversionError(message)
    # It should be an Exception subclass and store the message
    assert isinstance(err, Exception)
    assert str(err) == message


def test_raising_conversion_error_carries_original_message():
    message = "Unexpected error"
    with pytest.raises(ConversionError) as excinfo:
        raise ConversionError(message)
    assert str(excinfo.value) == message


def test_success_response_structure_and_values():
    data = {"html": "<p>test</p>"}
    resp = success_response(data)
    # Should have data equal to input and no error
    assert isinstance(resp, dict)
    assert resp.get("data") == data
    assert resp.get("error") is None


def test_error_response_structure_and_values():
    error_message = "Something went wrong"
    resp = error_response(error_message)
    # Should have no data and error equal to message
    assert isinstance(resp, dict)
    assert resp.get("data") is None
    assert resp.get("error") == error_message
