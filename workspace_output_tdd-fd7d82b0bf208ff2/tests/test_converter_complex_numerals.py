import pytest

from src.roman.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("XLII", 42),
        ("MCMXCIV", 1994),
        ("MMMDCCCLXXXVIII", 3888),
    ],
)
def test_complex_combined_numerals(roman, expected):
    """
    roman_to_int should correctly interpret complex Roman numerals that combine
    subtractive and additive notations.
    """
    assert roman_to_int(roman) == expected
