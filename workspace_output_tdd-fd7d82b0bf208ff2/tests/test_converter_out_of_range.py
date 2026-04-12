import pytest
from src.roman.converter import roman_to_int
from src.roman.exceptions import InvalidRomanNumeralError

@ pytest.mark.parametrize(
    "roman",
    [
        "MMMM",  # too many repeats of M
        "IM",    # invalid subtractive pair
    ],
)
def test_roman_to_int_invalid_for_out_of_range_like_inputs(roman):
    """
    roman_to_int should raise InvalidRomanNumeralError for inputs that are out-of-range
    due to invalid format (e.g., 'MMMM') or invalid subtractive notation (e.g., 'IM').
    """
    with pytest.raises(InvalidRomanNumeralError):
        roman_to_int(roman)
