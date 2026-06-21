import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize("s", [
    "",
    "MMMM",
])
def test_range_value_error_for_out_of_bounds(s):
    """
    Inputs that would produce values outside the allowed range [1,3999]
    should raise ValueError with message "Valor fora do intervalo permitido".
    """
    with pytest.raises(ValueError) as excinfo:
        roman_to_int(s)
    assert str(excinfo.value) == "Valor fora do intervalo permitido"