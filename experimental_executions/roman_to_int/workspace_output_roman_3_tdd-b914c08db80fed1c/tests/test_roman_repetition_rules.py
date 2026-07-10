import pytest
from src.roman import roman_to_int

@ pytest.mark.parametrize("illegal", [
    'IIII',  # I repeated 4 times
    'XXXX',  # X repeated 4 times
    'CCCC',  # C repeated 4 times
    'MMMM',  # M repeated 4 times (4000 out of range and illegal repetition)
    'VV',    # V repeated twice
    'LL',    # L repeated twice
    'DD',    # D repeated twice
])
def test_illegal_repetitions_raise_value_error(illegal):
    # Repetitions beyond allowed limits must raise ValueError
    with pytest.raises(ValueError):
        roman_to_int(illegal)
