import pytest
from pydantic import ValidationError
from schemas.request import RequestModel

def test_valid_request():
    data = {"markdown": "Hello World"}
    model = RequestModel(**data)
    assert model.markdown == "Hello World"


def test_empty_markdown_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        RequestModel(markdown="")
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("markdown",) and error["msg"].startswith("String should have at least 1 character")
        for error in errors
    )


def test_incorrect_type_markdown_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        RequestModel(markdown=123)
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("markdown",) and "Input should be a valid string" in error["msg"]
        for error in errors
    )
