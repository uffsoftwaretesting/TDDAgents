import pytest
from pydantic import ValidationError
from src.interfaces.models import ValidateCPFRequest, ValidateCPFResponse


def test_validate_cpf_request_valid_plain_cpf() -> None:
    req = ValidateCPFRequest(cpf='12345678909')
    assert req.cpf == '12345678909'


def test_validate_cpf_request_valid_masked_cpf() -> None:
    req = ValidateCPFRequest(cpf='123.456.789-09')
    assert req.cpf == '123.456.789-09'


@pytest.mark.parametrize(
    "cpf", [
        '1234567890',        # menos de 11
        '1'*15,              # mais de 14
    ],
)
def test_validate_cpf_request_length_constraints(cpf: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ValidateCPFRequest(cpf=cpf)
    errors = exc.value.errors()
    assert errors[0]['loc'] == ('cpf',)
    assert errors[0]['type'] in (
        'string_too_short',
        'string_too_long',
    )


def test_validate_cpf_request_invalid_characters() -> None:
    with pytest.raises(ValidationError) as exc:
        ValidateCPFRequest(cpf='1234567890A')
    errors = exc.value.errors()
    assert errors[0]['loc'] == ('cpf',)
    assert errors[0]['type'] == 'string_pattern_mismatch'


def test_validate_cpf_request_missing_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ValidateCPFRequest()  # type: ignore
    errors = exc.value.errors()
    assert errors[0]['loc'] == ('cpf',)
    assert errors[0]['type'] == 'missing'


def test_validate_cpf_request_none_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ValidateCPFRequest(cpf=None)  # type: ignore
    errors = exc.value.errors()
    assert errors[0]['loc'] == ('cpf',)
    assert (
        errors[0]['type'] == 'string_type'
        or errors[0]['type'].startswith('string_')
    )


def test_validate_cpf_response_valid_true() -> None:
    resp = ValidateCPFResponse(valid=True)
    assert resp.valid is True


def test_validate_cpf_response_valid_false() -> None:
    resp = ValidateCPFResponse(valid=False)
    assert resp.valid is False


def test_validate_cpf_response_missing_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ValidateCPFResponse()  # type: ignore
    errors = exc.value.errors()
    assert errors[0]['loc'] == ('valid',)
    assert errors[0]['type'] == 'missing'


def test_validate_cpf_response_ignores_extra_fields() -> None:
    resp = ValidateCPFResponse(valid=True, extra_field=123)  # type: ignore
    assert hasattr(resp, 'valid')
    assert not hasattr(resp, 'extra_field')
    assert resp.valid is True
