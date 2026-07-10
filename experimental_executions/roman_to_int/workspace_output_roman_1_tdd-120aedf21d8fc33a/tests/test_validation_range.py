import pytest
from roman_converter.converter import roman_to_int

@ pytest.mark.parametrize(
    "roman, expected",
    [
        ("MMMCMXCIX", 3999),  # highest valid Roman numeral
    ],
)
def test_maximum_supported_value(roman, expected):
    """
    O valor máximo suportado (3999) deve ser convertido corretamente.
    """
    assert roman_to_int(roman) == expected


def test_raises_value_error_when_result_greater_than_3999():
    """
    Qualquer numeral que resulte em valor >3999 deve gerar ValueError.
    Ex: MMMCMXCIXI = 4000
    """
    with pytest.raises(ValueError):
        roman_to_int("MMMCMXCIXI")
