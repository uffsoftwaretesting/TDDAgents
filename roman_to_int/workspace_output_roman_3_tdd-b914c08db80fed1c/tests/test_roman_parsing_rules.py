import pytest
from src.roman import roman_to_int

@ pytest.mark.parametrize("roman, expected", [
    ("III", 3),        # Somente adição: I+I+I
    ("LVIII", 58),    # L+V+I+I+I = 50+5+3
    ("MCMXCIV", 1994),# 1000 + (900) + (90) + (4)
    ("MMMCMXCIX", 3999),# maior valor dentro do limite
])
def test_parsing_additive_and_subtractive_combinations(roman, expected):
    # Verifica soma e subtração de acordo com as regras de parsing em loop
    assert roman_to_int(roman) == expected
