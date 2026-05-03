import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize("s, expected", [
    ("I", 1),
    ("iii", 3),               # lowercase normalization
    ("  iv", 4),             # leading spaces
    ("MCMXCIV", 1994),       # typical case
    ("mmmcmxcix", 3999),     # lowercase boundary max
    ("   Xl   ", 40),        # mixed case with spaces
])
def test_roman_to_int_valid_inputs(s, expected):
    """
    End-to-end valid Roman numerals should return correct integer values,
    including normalization by strip() and upper().
    """
    assert roman_to_int(s) == expected

@ pytest.mark.parametrize("s, expected_message", [
    ("", "Valor fora do intervalo permitido"),      # empty
    ("   ", "Valor fora do intervalo permitido"),  # whitespace only
    ("A", "Caractere inválido: A"),               # invalid character
    ("IIII", "Repetição inválida: IIII"),         # invalid repetition I
    ("VV", "Repetição inválida: VV"),             # invalid repetition V
    ("IL", "Subtração inválida: IL"),             # invalid subtraction pair
    ("VX", "Subtração inválida: VX"),             # another invalid pair
    ("MMMM", "Valor fora do intervalo permitido"),# out-of-range result
])
def test_roman_to_int_invalid_inputs(s, expected_message):
    """
    End-to-end invalid inputs should raise ValueError with correct message,
    covering empty, invalid chars, repetition, subtraction rules, and range.
    """
    with pytest.raises(ValueError) as excinfo:
        roman_to_int(s)
    assert str(excinfo.value) == expected_message
