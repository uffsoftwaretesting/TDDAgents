import pytest
from src.roman import roman_to_int


def test_roman_to_int_basic():
    # Aguardamos que 'I' seja convertido para 1
    assert roman_to_int('I') == 1


@pytest.mark.parametrize("invalid_input", [None, 123, []])
def test_roman_to_int_type_error(invalid_input):
    # Entradas não-str devem lançar TypeError
    with pytest.raises(TypeError):
        roman_to_int(invalid_input)


def test_roman_to_int_lowercase():
    # Entrada lowercase deve ser normalizada e convertida corretamente
    assert roman_to_int('mcmxciv') == 1994


def test_roman_to_int_empty_string():
    # String vazia deve lançar ValueError
    with pytest.raises(ValueError):
        roman_to_int('')


@pytest.mark.parametrize("invalid_roman", [
    'A',    # caractere completamente inválido
    'B1',   # combinação de inválido e dígito
    'AX',   # caractere inválido misturado
    'MMZ',  # caractere inválido no fim
    'I V',  # espaço não permitido
    'X*A'   # símbolo especial
])
def test_roman_to_int_invalid_characters(invalid_roman):
    # Qualquer caractere fora de ROMAN_VALUES deve lançar ValueError
    with pytest.raises(ValueError):
        roman_to_int(invalid_roman)
