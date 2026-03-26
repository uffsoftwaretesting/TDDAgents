import pytest
from datetime import datetime
from pydantic import ValidationError

from api.schemas.cpf_schemas import (
    CPFValidateRequestSchema,
    CPFValidateResponseSchema,
    ValidationEntrySchema,
    CPFHistoryResponseSchema,
    ValidationRecordSchema,
    PaginatedValidationsSchema,
)

def test_request_schema_valid():
    schema = CPFValidateRequestSchema(cpf="12345678901")
    assert schema.cpf == "12345678901"

@pytest.mark.parametrize("invalid_cpf", ["", "123", "abcdefghijk", "1234567890a", "123456789012"])
def test_request_schema_invalid_format(invalid_cpf):
    with pytest.raises(ValidationError):
        CPFValidateRequestSchema(cpf=invalid_cpf)


def test_validate_response_schema_valid():
    schema = CPFValidateResponseSchema(cpf="12345678901", valid=True)
    assert schema.cpf == "12345678901"
    assert schema.valid is True


def test_validate_response_schema_missing_fields():
    with pytest.raises(ValidationError):
        CPFValidateResponseSchema(cpf="12345678901")  # missing valid
    with pytest.raises(ValidationError):
        CPFValidateResponseSchema(valid=True)  # missing cpf


def test_entry_schema_valid_datetime():
    ts = datetime.now()
    schema = ValidationEntrySchema(timestamp=ts, valid=False)
    assert schema.timestamp == ts
    assert schema.valid is False

@pytest.mark.parametrize("invalid_ts", [123, "not-a-date", None])
def test_entry_schema_invalid_timestamp(invalid_ts):
    with pytest.raises(ValidationError):
        ValidationEntrySchema(timestamp=invalid_ts, valid=True)


def test_history_response_schema_with_results():
    ts = datetime(2020, 1, 1)
    entry_dict = {"timestamp": ts, "valid": True}
    history = CPFHistoryResponseSchema(cpf="12345678901", results=[entry_dict])
    assert history.cpf == "12345678901"
    assert len(history.results) == 1
    assert history.results[0].timestamp == ts
    assert history.results[0].valid is True


def test_history_response_schema_empty_results():
    history = CPFHistoryResponseSchema(cpf="12345678901", results=[])
    assert history.results == []


def test_record_schema_valid():
    ts = datetime.now()
    rec = ValidationRecordSchema(id=1, cpf="12345678901", valid=True, timestamp=ts)
    assert rec.id == 1
    assert rec.cpf == "12345678901"
    assert rec.valid is True
    assert rec.timestamp == ts

@pytest.mark.parametrize("kwargs", [
    {"id": "a", "cpf": "12345678901", "valid": True, "timestamp": datetime.now()},
    {"id": 1, "cpf": 123, "valid": True, "timestamp": datetime.now()},
    {"id": 1, "cpf": "12345678901", "valid": "yes", "timestamp": datetime.now()},
    {"id": 1, "cpf": "12345678901", "valid": True, "timestamp": "bad"},
])
def test_record_schema_invalid(kwargs):
    with pytest.raises(ValidationError):
        ValidationRecordSchema(**kwargs)


def test_paginated_schema_valid():
    ts = datetime.now()
    rec_dict = {"id": 1, "cpf": "12345678901", "valid": False, "timestamp": ts}
    page, size, total = 2, 5, 10
    schema = PaginatedValidationsSchema(items=[rec_dict], page=page, size=size, total=total)
    assert schema.page == page
    assert schema.size == size
    assert schema.total == total
    assert isinstance(schema.items, list)
    assert schema.items[0].id == 1

@pytest.mark.parametrize("bad_kwargs", [
    {"items": [], "page": 0, "size": 1, "total": 0},
    {"items": [], "page": 1, "size": 0, "total": 0},
    {"items": [], "page": -1, "size": 5, "total": 0},
])
def test_paginated_schema_invalid_page_size(bad_kwargs):
    with pytest.raises(ValidationError):
        PaginatedValidationsSchema(**bad_kwargs)
