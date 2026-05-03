import pytest
from roman_converter.converter import roman_to_int

def test_empty_string_raises_value_error():
    """
    Entrada vazia deve gerar ValueError.
    """
    with pytest.raises(ValueError):
        roman_to_int("")

@pytest.mark.parametrize(
    "roman",
    [
        "ABCD",   # caracteres fora do conjunto
        "Z",      # símbolo inválido
        "MMXIII@",# caractere especial
        "123",    # dígitos não permitidos
        "ix?",    # caracter inválido em lowercase
        "I V",    # espaço não permitido
    ],
)
def test_invalid_character_raises_value_error(roman):
    """
    Qualquer string contendo caracteres inválidos deve gerar ValueError.
    """
    with pytest.raises(ValueError):
        roman_to_int(roman)
