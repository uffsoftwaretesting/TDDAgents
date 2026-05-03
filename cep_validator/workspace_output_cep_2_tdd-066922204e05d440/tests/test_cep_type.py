import pytest
from src.cep_formatter import format_cep

def test_format_cep_invalid_type_none():
    with pytest.raises(TypeError) as exc:
        format_cep(None)
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_invalid_type_float():
    with pytest.raises(TypeError) as exc:
        format_cep(12.345678)
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_invalid_type_list():
    with pytest.raises(TypeError) as exc:
        format_cep([1,2,3,4,5,6,7,8])
    assert str(exc.value) == "Tipo inválido: esperado str ou int."

def test_format_cep_invalid_type_dict():
    with pytest.raises(TypeError) as exc:
        format_cep({"cep":"12345678"})
    assert str(exc.value) == "Tipo inválido: esperado str ou int."