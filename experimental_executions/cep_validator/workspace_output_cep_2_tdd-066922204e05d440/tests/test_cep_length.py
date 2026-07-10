import pytest
from src.cep_formatter import format_cep

def test_cep_length_short():
    with pytest.raises(ValueError) as exc:
        format_cep("1234")
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos."

def test_cep_length_long():
    with pytest.raises(ValueError) as exc:
        format_cep("123456789")
    assert str(exc.value) == "CEP deve ter exatamente 8 dígitos."