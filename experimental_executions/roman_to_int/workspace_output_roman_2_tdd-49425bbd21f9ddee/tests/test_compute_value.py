import pytest
from roman_converter.converter import compute_value

@ pytest.mark.parametrize("s, expected", [
    ("III", 3),
    ("IV", 4),
    ("IX", 9),
    ("LVIII", 58),
    ("MCMXCIV", 1994),
    ("MMMCMXCIX", 3999),
])
def test_compute_value_typical_and_edge_cases(s, expected):
    """
    Typical and edge Roman numeral cases should map to their integer values.
    """
    assert compute_value(s) == expected
