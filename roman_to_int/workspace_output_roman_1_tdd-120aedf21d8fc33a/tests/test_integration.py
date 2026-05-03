import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize("roman, expected", [
    ("III", 3),
    ("IV", 4),
    ("IX", 9),
    ("LVIII", 58),
    ("MCMXCIV", 1994),
    ("MMMDCCCLXXXVIII", 3888),
    ("mmmdccclxxxviii", 3888),  # case-insensitive
    ("MDCCLXXVI", 1776),          # mistura de aditivo e subtrativo
    ("I", 1),
    ("MMMCMXCIX", 3999),          # limite máximo
])
def test_integration_valid_numerals(roman, expected):
    """
    Casos válidos de conversão mistas: aditivo, subtrativo, compostos e limites.
    """
    assert roman_to_int(roman) == expected

@ pytest.mark.parametrize("roman", [
    "",                   # string vazia
    "ABCD",               # caracteres inválidos
    "IIII",               # repetição excessiva de I
    "VV",                 # repetição não permitida de V
    "IC",                 # subtração inválida
    "XM",                 # subtração inválida
    "MMMCMXCIXI",         # resultado maior que 3999
    "i v",                # espaço e caracteres minúsculos inválidos
])
def test_integration_invalid_numerals(roman):
    """
    Casos inválidos gerais: vazio, caracteres inválidos, repetições, subtrações ilegais, fora de alcance.
    """
    with pytest.raises(ValueError):
        roman_to_int(roman)