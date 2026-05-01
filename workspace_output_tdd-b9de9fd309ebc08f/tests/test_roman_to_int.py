import pytest
from roman_converter import roman_to_int


def test_roman_to_int_basic_I():
    # Conversão básica de numeral romano
    assert roman_to_int('I') == 1


def test_empty_string_raises_value_error():
    # Entrada vazia deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        roman_to_int('')
    assert str(excinfo.value) == "Input string is empty"


@ pytest.mark.parametrize("roman, expected", [
    ("III", 3),         # simples aditivo
    ("IV", 4),          # subtração básica
    ("IX", 9),          # outra subtração
    ("LVIII", 58),      # misto aditivo e subtrativo
    ("MCMXCIV", 1994),  # numeral complexo grande
    ("MMMCMXCIX", 3999) # maior valor permitido
])
def test_roman_to_int_valid_numerals(roman, expected):
    # Deve converter numerais bem-formados dentro de 1–3999
    assert roman_to_int(roman) == expected


@ pytest.mark.parametrize("roman, invalid_char", [
    ("A", "A"),
    ("BA", "B"),
    ("IAX", "A"),
])
def test_roman_to_int_invalid_characters(roman, invalid_char):
    # Caracteres não permitidos devem lançar ValueError indicando o primeiro inválido
    with pytest.raises(ValueError) as excinfo:
        roman_to_int(roman)
    assert str(excinfo.value) == f"Invalid character: {invalid_char}"


@ pytest.mark.parametrize("roman, char", [
    ("IIII", "I"),
    ("XXXX", "X"),
    ("CCCC", "C"),
    ("MMMM", "M"),
    ("VV", "V"),
    ("LL", "L"),
    ("DD", "D"),
])
def test_roman_to_int_excessive_repetitions(roman, char):
    # Excesso de repetições consecutivas deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        roman_to_int(roman)
    assert str(excinfo.value) == f"Too many repetitions: {char}"


@ pytest.mark.parametrize("roman", [
    "IL",
    "IC",
    "VX",
    "XM",
    "IM",
    "LC",
    "DM",
])
def test_roman_to_int_invalid_subtractive_pairs(roman):
    # Pares subtrativos inválidos devem lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        roman_to_int(roman)
    assert str(excinfo.value) == f"Invalid subtractive pair: {roman}"


def test_roman_to_int_result_out_of_range():
    # Resultado maior que 3999 deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        roman_to_int("MMMCMXCIXI")
    assert str(excinfo.value) == "Result out of range (1–3999): 4000"