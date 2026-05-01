import pytest
from pydantic import ValidationError

from src.presentation.schemas import ValidateCpfRequest, ValidateCpfResponse


def test_validate_cpf_request_with_valid_payload():
    req = ValidateCpfRequest(cpf="12345678909")
    assert req.cpf == "12345678909"


def test_validate_cpf_request_missing_cpf_raises_validation_error():
    with pytest.raises(ValidationError):
        ValidateCpfRequest()


def test_validate_cpf_request_null_cpf_raises_validation_error():
    with pytest.raises(ValidationError):
        ValidateCpfRequest(cpf=None)


def test_validate_cpf_response_with_valid_payload():
    res = ValidateCpfResponse(valid=True)
    assert res.valid is True


def test_validate_cpf_response_missing_valid_raises_validation_error():
    with pytest.raises(ValidationError):
        ValidateCpfResponse()


def test_validate_cpf_response_null_valid_raises_validation_error():
    with pytest.raises(ValidationError):
        ValidateCpfResponse(valid=None)
