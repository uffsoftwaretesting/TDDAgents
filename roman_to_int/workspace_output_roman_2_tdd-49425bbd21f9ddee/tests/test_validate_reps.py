import pytest
from roman_converter.converter import validate_repetition

@ pytest.mark.parametrize("s", [
    "IIII", "XXXX", "CCCC", "MMMM"
])
def test_validate_repetition_max_three_error_for_ixcm(s):
    """
    I, X, C, M must not repeat more than three times consecutively.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_repetition(s)
    assert str(excinfo.value) == f"Repetição inválida: {s}"

@ pytest.mark.parametrize("s", [
    "VV", "LL", "DD"
])
def test_validate_repetition_no_repeat_error_for_vld(s):
    """
    V, L, D must not repeat consecutively.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_repetition(s)
    assert str(excinfo.value) == f"Repetição inválida: {s}"