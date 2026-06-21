import pytest
from pydantic import ValidationError

from src.interfaces.schemas import ValidateCpfRequest, ValidateCpfResponse


def test_validate_cpf_request_empty_payload():
    # Missing 'cpf' field should raise a ValidationError
    with pytest.raises(ValidationError) as excinfo:
        ValidateCpfRequest(**{})
    # Pydantic v2 uses 'Field required' in error messages
    assert "Field required" in str(excinfo.value)


@pytest.mark.parametrize("cpf_input", [
    "52998224725",      # unmasked numeric
    "529.982.247-25",   # masked format
])
def test_validate_cpf_request_valid_inputs(cpf_input):
    # Valid inputs should be sanitized to digits-only
    req = ValidateCpfRequest(cpf=cpf_input)
    assert req.cpf == "52998224725"


@pytest.mark.parametrize("cpf_input", [
    "123.456.78a-09",   # contains a letter
    "",                 # empty string
    "123456789",        # too short after sanitization
])
def test_validate_cpf_request_invalid_format(cpf_input):
    # Invalid formats should raise a ValidationError
    with pytest.raises(ValidationError):
        ValidateCpfRequest(cpf=cpf_input)


def test_validate_cpf_response():
    # The response model should accept boolean values
    resp_true = ValidateCpfResponse(valid=True)
    assert resp_true.valid is True

    resp_false = ValidateCpfResponse(valid=False)
    assert resp_false.valid is False