import pytest

from src.roman.converter import roman_to_int

@ pytest.mark.parametrize(
    "symbol, expected",
    [
        ("I", 1),
        ("V", 5),
        ("X", 10),
        ("L", 50),
        ("C", 100),
        ("D", 500),
        ("M", 1000),
        ("i", 1),
        ("v", 5),
        ("x", 10),
        ("l", 50),
        ("c", 100),
        ("d", 500),
        ("m", 1000),
    ],
)
def test_single_roman_symbols_case_insensitive(symbol, expected):
    """
    roman_to_int should return the correct integer for single Roman numeral symbols,
    regardless of case.
    """
    assert roman_to_int(symbol) == expected
