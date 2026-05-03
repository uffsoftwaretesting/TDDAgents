import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman",
    [
        "IL",  # I before L invalid
        "IC",  # I before C invalid
        "VX",  # V before X invalid
        "XM",  # X before M invalid
        "XD",  # X before D invalid
        "LC",  # L before C invalid
        "DM",  # D before M invalid
        "ID",  # I before D invalid
        "IM",  # I before M invalid
    ],
)
def test_invalid_subtractive_combinations_raise_value_error(roman):
    """
    Combinações de subtração não permitidas (não listadas nas regras) devem gerar ValueError.
    """
    with pytest.raises(ValueError):
        roman_to_int(roman)
