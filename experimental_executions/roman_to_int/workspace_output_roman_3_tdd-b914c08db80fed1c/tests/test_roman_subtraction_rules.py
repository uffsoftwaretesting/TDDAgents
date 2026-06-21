import pytest
from src.roman import roman_to_int

@pytest.mark.parametrize("roman, expected", [
    ("IV", 4),
    ("IX", 9),
    ("XL", 40),
    ("XC", 90),
    ("CD", 400),
    ("CM", 900),
])
def test_valid_subtractive_pairs(roman, expected):
    # Pares de subtração válidos devem retornar o valor correto
    assert roman_to_int(roman) == expected

@pytest.mark.parametrize("invalid", [
    "IIV",  # subtração dupla não permitida
    "IL",   # subtração inválida
    "IC",   # subtração inválida
    "ID",   # subtração inválida
    "IM",   # subtração inválida
    "XXL",  # subtração inválida (X antes de L)
    "VX",   # subtração inválida
    "VL",   # subtração inválida
    "VC",   # subtração inválida
    "VD",   # subtração inválida
    "VM",   # subtração inválida
    "XD",   # subtração inválida
    "XM",   # subtração inválida
])
def test_invalid_subtractive_pairs_raise_value_error(invalid):
    # Pares de subtração inválidos devem lançar ValueError
    with pytest.raises(ValueError):
        roman_to_int(invalid)
