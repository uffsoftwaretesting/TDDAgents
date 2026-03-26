import pytest
from datetime import datetime, timezone, timedelta

from domain.entities.cpf import CPF
from domain.entities.cpf_validation import CPFValidation


def get_utc_now():
    return datetime.now(timezone.utc)


def test_init_with_all_fields():
    # Deve aceitar todos os atributos informados corretamente
    id_value = 10
    cpf_obj = CPF("12345678901")
    valid_flag = True
    ts = get_utc_now()
    validation = CPFValidation(id=id_value, cpf=cpf_obj, valid=valid_flag, timestamp=ts)

    assert validation.id == id_value
    assert validation.cpf is cpf_obj
    assert validation.valid is valid_flag
    assert validation.timestamp is ts


def test_init_without_id():
    # Deve aceitar ausência de id (opcional)
    cpf_obj = CPF("12345678901")
    valid_flag = False
    ts = get_utc_now()
    validation = CPFValidation(cpf=cpf_obj, valid=valid_flag, timestamp=ts)

    assert validation.id is None
    assert validation.cpf is cpf_obj
    assert validation.valid is valid_flag
    assert validation.timestamp is ts

@pytest.mark.parametrize("invalid_id", ["a", 1.5, object()])
def test_invalid_id_type_raises_type_error(invalid_id):
    # id deve ser int ou None
    cpf_obj = CPF("12345678901")
    ts = get_utc_now()
    with pytest.raises(TypeError):
        CPFValidation(id=invalid_id, cpf=cpf_obj, valid=True, timestamp=ts)

@pytest.mark.parametrize("invalid_cpf", [123, "12345678901", None])
def test_invalid_cpf_type_raises_type_error(invalid_cpf):
    # cpf deve ser instância de CPF
    ts = get_utc_now()
    with pytest.raises(TypeError):
        CPFValidation(cpf=invalid_cpf, valid=True, timestamp=ts)

@pytest.mark.parametrize("invalid_valid", [1, "true", None])
def test_invalid_valid_type_raises_type_error(invalid_valid):
    # valid deve ser bool
    cpf_obj = CPF("12345678901")
    ts = get_utc_now()
    with pytest.raises(TypeError):
        CPFValidation(cpf=cpf_obj, valid=invalid_valid, timestamp=ts)

@pytest.mark.parametrize("invalid_ts", [datetime.now(), "2020-01-01T00:00:00Z", None])
def test_invalid_timestamp_type_raises_type_error(invalid_ts):
    # timestamp deve ser datetime com tzinfo UTC
    cpf_obj = CPF("12345678901")
    with pytest.raises(TypeError):
        CPFValidation(cpf=cpf_obj, valid=True, timestamp=invalid_ts)


def test_non_utc_timestamp_raises_value_error():
    # timestamp deve ter tzinfo UTC, não qualquer outro fuso
    cpf_obj = CPF("12345678901")
    ts_non_utc = datetime.now(timezone(timedelta(hours=1)))
    with pytest.raises(ValueError):
        CPFValidation(cpf=cpf_obj, valid=True, timestamp=ts_non_utc)
