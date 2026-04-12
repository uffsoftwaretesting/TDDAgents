import pytest

from src.roman.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("II", 2),
        ("XXII", 22),
        ("MDCLXVI", 1666),
        # também podemos testar versões minúsculas por case-insensitivity
        ("ii", 2),
        ("xxii", 22),
        ("mdclxvi", 1666),
    ],
)
def test_additive_roman_numerals_without_subtraction(roman, expected):
    """
    roman_to_int deve somar corretamente numerais em ordem decrescente sem operadores de subtração.
    """
    assert roman_to_int(roman) == expected
