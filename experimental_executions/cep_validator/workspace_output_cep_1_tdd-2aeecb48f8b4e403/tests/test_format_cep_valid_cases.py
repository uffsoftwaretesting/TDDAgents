import pytest
from src.cep_formatter import format_cep

@ pytest.mark.parametrize(
    "input_cep, expected",
    [
        ("24350310", "24350-310"),
        (12345678, "12345-678"),
        ("00000000", "00000-000"),
        ("00123456", "00123-456"),
    ],
)
def test_format_cep_valid_cases(input_cep, expected):
    """
    Testa formatação correta do CEP para casos válidos,
    inserindo hífen entre o quinto e sexto dígitos.
    """
    resultado = format_cep(input_cep)
    assert isinstance(resultado, str)
    assert resultado == expected
