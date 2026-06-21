import pytest
from pydantic import ValidationError
from schemas.error import ErrorModel

def test_valid_error_model():
    data = {"detail": "An error occurred", "code": 400}
    model = ErrorModel(**data)
    assert model.detail == "An error occurred"
    assert model.code == 400


def test_missing_detail_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ErrorModel(code=500)
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("detail",) and error["msg"].startswith("Field required")
        for error in errors
    )


def test_missing_code_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ErrorModel(detail="Error")
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("code",) and error["msg"].startswith("Field required")
        for error in errors
    )


def test_incorrect_type_detail_raises_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        ErrorModel(detail=123, code=500)
    errors = exc_info.value.errors()
    assert any(
        error["loc"] == ("detail",) and "Input should be a valid string" in error["msg"]
        for error in errors
    )


def test_code_string_is_coerced_to_int():
    model = ErrorModel(detail="Error", code="500")
    assert isinstance(model.code, int)
    assert model.code == 500
