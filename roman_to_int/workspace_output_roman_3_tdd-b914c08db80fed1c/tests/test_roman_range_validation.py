import pytest
from src.roman import roman_to_int


def test_range_boundaries_min_and_max():
    # Valores de fronteira dentro do domínio
    assert roman_to_int('I') == 1
    assert roman_to_int('MMMCMXCIX') == 3999


def test_result_out_of_domain_raises_value_error():
    # "MMMM" representa 4000 -> fora do domínio
    with pytest.raises(ValueError):
        roman_to_int('MMMM')
    # Combinação válida logicamente que soma 3999 + 1 = 4000 -> fora do domínio
    with pytest.raises(ValueError):
        roman_to_int('MMMCMXCIXI')