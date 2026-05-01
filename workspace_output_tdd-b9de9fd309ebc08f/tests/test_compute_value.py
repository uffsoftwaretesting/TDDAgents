import pytest

from roman_converter.calculator import compute_value
from roman_converter.constants import values_map, valid_subtractions

@ pytest.mark.parametrize("roman, expected", [
    ("III", 3),         # simple additive
    ("IV", 4),          # basic subtraction
    ("IX", 9),          # another subtraction
    ("LVIII", 58),      # mixed additive and subtraction
    ("MCMXCIV", 1994),  # large complex numeral
])
def test_compute_value_various_numerals(roman, expected):
    """
    compute_value should correctly calculate the integer value
    of well-formed Roman numerals using the provided maps.
    """
    result = compute_value(roman, values_map, valid_subtractions)
    assert result == expected
