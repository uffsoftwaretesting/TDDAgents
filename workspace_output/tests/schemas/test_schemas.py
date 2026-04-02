import pytest
from pydantic import ValidationError
from uuid import uuid4
from datetime import datetime, timezone

from src.schemas.request import CPFValidationRequestSchema, PaginationParams
from src.schemas.response import CPFValidationResponseSchema, ValidationListResponseSchema


def test_request_schema_valid():
    data = {'cpf': '12345678901'}
    schema = CPFValidationRequestSchema(**data)
    assert schema.cpf == data['cpf']


def test_request_schema_invalid_short_length():
    with pytest.raises(ValidationError) as exc_info:
        CPFValidationRequestSchema(cpf='123')
    errors = exc_info.value.errors()
    assert any(err['loc'] == ('cpf',) for err in errors)


def test_request_schema_invalid_non_numeric():
    with pytest.raises(ValidationError):
        CPFValidationRequestSchema(cpf='1234567890a')


def test_pagination_params_valid():
    params = PaginationParams(page=1, size=10)
    assert params.page == 1
    assert params.size == 10


def test_pagination_params_invalid_page_zero():
    with pytest.raises(ValidationError):
        PaginationParams(page=0, size=10)


def test_pagination_params_invalid_size_zero():
    with pytest.raises(ValidationError):
        PaginationParams(page=1, size=0)


def test_response_schema_valid():
    id_str = str(uuid4())
    created = datetime.now(timezone.utc).isoformat()
    data = {
        'id': id_str,
        'cpf': '12345678901',
        'is_valid': True,
        'created_at': created
    }
    schema = CPFValidationResponseSchema(**data)
    assert str(schema.id) == id_str
    assert schema.cpf == data['cpf']
    assert schema.is_valid is True
    assert isinstance(schema.created_at, datetime)


def test_response_schema_invalid_id():
    with pytest.raises(ValidationError):
        CPFValidationResponseSchema(
            id='not-a-uuid',
            cpf='12345678901',
            is_valid=True,
            created_at=datetime.now(timezone.utc).isoformat()
        )


def test_response_schema_invalid_created_at():
    id_str = str(uuid4())
    with pytest.raises(ValidationError):
        CPFValidationResponseSchema(
            id=id_str,
            cpf='12345678901',
            is_valid=True,
            created_at='invalid-date'
        )


def test_validation_list_response_schema_valid():
    id1 = str(uuid4())
    id2 = str(uuid4())
    created = datetime.now(timezone.utc).isoformat()
    items = [
        {'id': id1, 'cpf': '11111111111', 'is_valid': True, 'created_at': created},
        {'id': id2, 'cpf': '22222222222', 'is_valid': False, 'created_at': created}
    ]
    schema = ValidationListResponseSchema(items=items, page=2, size=5)
    assert schema.page == 2
    assert schema.size == 5
    assert len(schema.items) == 2
    assert str(schema.items[0].id) == id1


def test_validation_list_response_schema_invalid_items():
    with pytest.raises(ValidationError):
        ValidationListResponseSchema(items=[{'cpf': '12345678901'}], page=1, size=1)


def test_validation_list_response_schema_invalid_page_size():
    with pytest.raises(ValidationError):
        ValidationListResponseSchema(items=[], page=0, size=0)
