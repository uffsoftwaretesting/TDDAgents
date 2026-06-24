import pytest
from pydantic import ValidationError
from app.schemas.markdown import RequestModel, ResponseModel


def test_requestmodel_missing_field():
    # Missing required markdown field
    with pytest.raises(ValidationError) as excinfo:
        RequestModel()
    errors = excinfo.value.errors()
    assert any(
        err['loc'] == ('markdown',) and err['msg'].startswith('Field required')
        for err in errors
    )


def test_requestmodel_empty_markdown():
    # Empty string should trigger custom "not empty" error
    with pytest.raises(ValidationError) as excinfo:
        RequestModel(markdown="")
    errors = excinfo.value.errors()
    assert errors[0]['loc'] == ('markdown',)
    assert errors[0]['msg'].endswith('Markdown content must not be empty.')


def test_requestmodel_max_length_exceeded():
    # Exceeding 10000 chars should trigger custom "too large" error
    too_long = 'a' * 10001
    with pytest.raises(ValidationError) as excinfo:
        RequestModel(markdown=too_long)
    errors = excinfo.value.errors()
    assert errors[0]['loc'] == ('markdown',)
    assert errors[0]['msg'].endswith('Markdown content too large (max 10000 chars).')


def test_requestmodel_valid_max_length():
    # Exactly 10000 chars is allowed
    valid = 'a' * 10000
    rm = RequestModel(markdown=valid)
    assert rm.markdown == valid


def test_responsemodel_valid_data_and_error_default():
    # Only data provided, error should default to None
    resp = ResponseModel(data={'html': '<p>test</p>'})
    assert resp.data.html == '<p>test</p>'
    assert resp.error is None


def test_responsemodel_missing_data_field():
    # Missing data should raise ValidationError
    with pytest.raises(ValidationError) as excinfo:
        ResponseModel(error=None)
    errors = excinfo.value.errors()
    assert any(
        err['loc'] == ('data',) and err['msg'].startswith('Field required')
        for err in errors
    )


def test_responsemodel_invalid_html_type():
    # html must be a string
    with pytest.raises(ValidationError) as excinfo:
        ResponseModel(data={'html': 123}, error=None)
    errors = excinfo.value.errors()
    assert errors[0]['loc'] == ('data', 'html')
    assert 'Input should be a valid string' in errors[0]['msg']


def test_responsemodel_invalid_error_type():
    # error must be a string if provided
    with pytest.raises(ValidationError) as excinfo:
        ResponseModel(data={'html': '<p></p>'}, error=123)
    errors = excinfo.value.errors()
    assert errors[0]['loc'] == ('error',)
    assert 'Input should be a valid string' in errors[0]['msg']
