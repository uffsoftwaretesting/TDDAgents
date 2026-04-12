import pytest
from src.roman.converter import roman_to_int
from src.roman.exceptions import InvalidRomanNumeralError

@pytest.mark.parametrize("input_value", [None, ""])
def test_roman_to_int_null_or_empty_raises_invalid_roman_numeral_error(input_value):
    """
    roman_to_int should raise InvalidRomanNumeralError when input is None or empty string.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(input_value)

@pytest.mark.parametrize("input_value", ["ABCD", "X1V!", " "])
def test_roman_to_int_invalid_characters_raises_invalid_roman_numeral_error(input_value):
    """
    roman_to_int should raise InvalidRomanNumeralError when input contains non-Roman characters or whitespace.
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(input_value)