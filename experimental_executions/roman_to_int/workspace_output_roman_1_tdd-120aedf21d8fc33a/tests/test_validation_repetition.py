import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman",
    [
        "IIII", "XXXX", "CCCC", "MMMM",
        "iiii", "xxxx", "cccc", "mmmm",
    ],
)
def test_repeatable_symbols_more_than_three_consecutive_raise_value_error(roman):
    """
    Símbolos I, X, C, M podem se repetir no máximo três vezes.
    Quatro repetições consecutivas devem gerar ValueError.
    """
    with pytest.raises(ValueError):
        roman_to_int(roman)

@ pytest.mark.parametrize(
    "roman",
    [
        "VV", "LL", "DD",
        "vv", "ll", "dd",
    ],
)
def test_non_repeatable_symbols_consecutive_raise_value_error(roman):
    """
    Símbolos V, L, D não podem se repetir consecutivamente.
    Qualquer repetição deve gerar ValueError.
    """
    with pytest.raises(ValueError):
        roman_to_int(roman)
