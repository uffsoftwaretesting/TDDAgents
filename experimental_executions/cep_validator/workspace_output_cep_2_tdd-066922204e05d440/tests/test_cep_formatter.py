import pytest
from src.cep_formatter import format_cep

def test_format_cep_valid_str():
    assert format_cep("24350310") == "24350-310"

def test_format_cep_valid_int():
    assert format_cep(12345678) == "12345-678"

def test_format_cep_invalid_type_none():
    with pytest.raises(TypeError) as exc:
        format_cep(None)
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_invalid_type_float():
    with pytest.raises(TypeError) as exc:
        format_cep(12.345678)
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_non_numeric_string():
    with pytest.raises(ValueError) as exc:
        format_cep("1234abcd")
    assert str(exc.value) == "CEP deve conter apenas dígitos."

def test_format_cep_wrong_length_short():
    with pytest.raises(ValueError) as exc:
        format_cep("1234")
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos."

def test_format_cep_wrong_length_long():
    with pytest.raises(ValueError) as exc:
        format_cep("123456789")
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos."