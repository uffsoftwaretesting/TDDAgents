import pytest

from roman_converter.validation import validate_repetitions_and_subtractions
from roman_converter.constants import max_repeats, valid_subtractions

@ pytest.mark.parametrize("roman,char", [
    ("IIII", "I"),
    ("XXXX", "X"),
    ("CCCC", "C"),
    ("MMMM", "M"),
    ("VV", "V"),
    ("LL", "L"),
    ("DD", "D"),
])
def test_validate_repetitions_excessive(roman, char):
    """
    Excesso de repetições consecutivas deve lançar ValueError indicando o caractere repetido.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_repetitions_and_subtractions(roman, max_repeats, valid_subtractions)
    assert str(excinfo.value) == f"Too many repetitions: {char}"


@ pytest.mark.parametrize("roman", [
    "IL",
    "IC",
    "VX",
    "XM",
    "IM",
    "LC",
    "DM",
])
def test_validate_subtractive_invalid_pairs(roman):
    """
    Pares subtrativos inválidos devem lançar ValueError indicando o par inválido.
    """
    with pytest.raises(ValueError) as excinfo:
        validate_repetitions_and_subtractions(roman, max_repeats, valid_subtractions)
    assert str(excinfo.value) == f"Invalid subtractive pair: {roman}"
