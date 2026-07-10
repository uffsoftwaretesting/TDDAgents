import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("IV", 4),
        ("IX", 9),
        ("XL", 40),
        ("XC", 90),
        ("CD", 400),
        ("CM", 900),
    ],
)
def test_subtractive_basic_pairs(roman, expected):
    """
    Casos de subtração simples:
    IV→4, IX→9, XL→40, XC→90, CD→400, CM→900
    """
    assert roman_to_int(roman) == expected


def test_composite_subtractive_mcmxciv():
    """
    Composição complexa com múltiplos pares subtrativos:
    MCMXCIV → 1994 (case-insensitive)
    """
    assert roman_to_int("MCMXCIV") == 1994
    assert roman_to_int("mcmxciv") == 1994
