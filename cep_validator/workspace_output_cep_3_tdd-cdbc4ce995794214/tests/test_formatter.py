import pytest
from cep_formatter.formatter import format_cep


def test_format_cep_exists():
    # A função deve existir e ser chamável
    assert callable(format_cep)


@pytest.mark.parametrize("invalid_input", [None, 12.345678, [], {}])
def test_format_cep_raises_type_error_for_invalid_type(invalid_input):
    # Tipos que não sejam str ou int devem gerar TypeError com mensagem exata
    with pytest.raises(TypeError) as excinfo:
        format_cep(invalid_input)
    assert str(excinfo.value) == "CEP deve ser string ou inteiro"


@pytest.mark.parametrize("invalid_input", ["abcdefgh", "12.345-678", -12345678])
def test_format_cep_raises_value_error_for_non_digit_characters(invalid_input):
    # Entradas que não contenham apenas dígitos devem gerar ValueError com mensagem exata
    with pytest.raises(ValueError) as excinfo:
        format_cep(invalid_input)
    assert str(excinfo.value) == "CEP deve conter apenas dígitos"


def test_format_cep_happy_path_string():
    # String de 8 dígitos deve ser formatada corretamente no padrão XXXXX-XXX
    assert format_cep('12345678') == '12345-678'


@pytest.mark.parametrize(
    "invalid_length_input",
    ['1234', 1234, '123456789', 123456789]
)
def test_format_cep_raises_value_error_for_incorrect_length(invalid_length_input):
    # Entradas com comprimento diferente de 8 devem gerar ValueError com mensagem exata
    with pytest.raises(ValueError) as excinfo:
        format_cep(invalid_length_input)
    assert str(excinfo.value) == "CEP deve ter exatamente 8 dígitos"


@pytest.mark.parametrize(
    "valid_input, expected",
    [
        ('00000000', '00000-000'),
        (10000000, '10000-000'),
    ]
)
def test_format_cep_preserves_leading_zeros_and_handles_integer(valid_input, expected):
    # Zeros à esquerda em string e inteiros devem ser tratados corretamente
    assert format_cep(valid_input) == expected
