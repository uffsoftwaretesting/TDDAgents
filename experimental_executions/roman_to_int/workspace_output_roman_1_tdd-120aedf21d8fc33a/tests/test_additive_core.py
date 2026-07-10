import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("I", 1),
        ("II", 2),
        ("III", 3),
        ("VI", 6),
        ("XV", 15),
    ],
)
def test_additive_basic_numerals(roman, expected):
    """
    Casos simples sem subtração: 
    I→1, II→2, III→3, VI→6, XV→15
    """
    assert roman_to_int(roman) == expected
