import pytest
from pydantic import ValidationError
from schemas.response import ResponseModel

def test_valid_response_with_message():
    data = {"html": "<p>Test</p>", "message": "Conversion successful"}
    model = ResponseModel(**data)
    assert model.html == "<p>Test</p>"
    assert model.message == "Conversion successful"


def test_valid_response_without_message():
    data = {"html": "<p>Test</p>"}
    model = ResponseModel(**data)
    assert model.html == "<p>Test</p>"
    assert model.message is None


def test_missing_html_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ResponseModel(message="msg")
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("html",) and error["msg"].startswith("Field required")
        for error in errors
    )


def test_incorrect_type_html_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ResponseModel(html=123)
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("html",) and "Input should be a valid string" in error["msg"]
        for error in errors
    )


def test_incorrect_type_message_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ResponseModel(html="<p/>", message=123)
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("message",) and "Input should be a valid string" in error["msg"]
        for error in errors
    )
