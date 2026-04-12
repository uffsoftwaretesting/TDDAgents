import pytest
from src.roman.converter import roman_to_int
from src.roman.exceptions import InvalidRomanNumeralError

@ pytest.mark.parametrize(
    "roman",
    [
        "IIII",  # I repeated 4 times
        "XXXX",  # X repeated 4 times
        "CCCC",  # C repeated 4 times
        "MMMM",  # M repeated 4 times
        "iiii",  # lowercase I repeated 4 times (case-insensitive)
        "xxxx",  # lowercase X repeated 4 times
        "cccc",  # lowercase C repeated 4 times
        "mmmm",  # lowercase M repeated 4 times
    ],
)
def test_too_many_repeats_of_I_X_C_M_raise_invalid(roman):
    """
    roman_to_int should raise InvalidRomanNumeralError when I, X, C, M repeat more than 3 times.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(roman)

@ pytest.mark.parametrize(
    "roman",
    [
        "VV",  # V repeated twice
        "LL",  # L repeated twice
        "DD",  # D repeated twice
        "vv",  # lowercase V repeated twice
        "ll",  # lowercase L repeated twice
        "dd",  # lowercase D repeated twice
    ],
)
def test_repeated_V_L_D_raise_invalid(roman):
    """
    roman_to_int should raise InvalidRomanNumeralError when V, L, D repeat consecutively.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(roman)
