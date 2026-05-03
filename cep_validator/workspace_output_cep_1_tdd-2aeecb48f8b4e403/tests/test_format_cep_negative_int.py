import pytest
from src.cep_formatter import format_cep

@ pytest.mark.parametrize("invalid", [
    -12345678,
    -87654321,
])
def test_format_cep_raises_value_error_para_int_negativo(invalid):
    """
    Testa que inteiros negativos produzem '-' na string e disparam ValueError
    com mensagem "CEP deve conter apenas dígitos".
    """
    with pytest.raises(ValueError) as exc:
        format_cep(invalid)
    assert str(exc.value) == "CEP deve conter apenas dígitos"