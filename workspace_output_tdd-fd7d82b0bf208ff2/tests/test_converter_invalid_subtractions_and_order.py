import pytest
from src.roman.converter import roman_to_int
from src.roman.exceptions import InvalidRomanNumeralError

@ pytest.mark.parametrize(
    "roman",
    [
        "IL",  # I antes de L não permitido
        "il",
        "XD",  # X antes de D não permitido
        "xd",
        "XM",  # X antes de M não permitido
        "xm",
    ],
)
def test_invalid_subtractive_pairs_raise_invalid(roman):
    """
    roman_to_int should raise InvalidRomanNumeralError for invalid subtractive pairs.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(roman)

@ pytest.mark.parametrize(
    "roman",
    [
        "VX",  # V antes de X não é válida subtração
        "vx",
        "LC",  # L antes de C não é válida subtração
        "lc",
    ],
)
def test_invalid_symbol_order_raise_invalid(roman):
    """
    roman_to_int should raise InvalidRomanNumeralError for improper ordering of symbols.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(roman)
