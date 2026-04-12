import pytest

from src.roman.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("IV", 4),
        ("IX", 9),
        ("XL", 40),
        ("XC", 90),
        ("CD", 400),
        ("CM", 900),
        ("iv", 4),
        ("ix", 9),
        ("xl", 40),
        ("xc", 90),
        ("cd", 400),
        ("cm", 900),
    ],
)
def test_subtractive_notation_standard_patterns(roman, expected):
    """
    roman_to_int should correctly handle standard subtractive notation (case-insensitive):
    IV→4, IX→9, XL→40, XC→90, CD→400, CM→900.
    """
    assert roman_to_int(roman) == expected
