import math
import pytest
from temperature_converter.converter import fahrenheit_to_celsius

def test_nan_returns_nan():
    nan_input = float('nan')
    # Deve retornar nan quando a entrada é nan
    result = fahrenheit_to_celsius(nan_input)
    assert math.isnan(result), "Expected result to be NaN"

@pytest.mark.parametrize("inf_input, expected_sign", [
    (float('inf'), 1.0),
    (float('-inf'), -1.0),
])
def test_inf_returns_signed_infinite(inf_input, expected_sign):
    # Deve retornar infinito com sinal igual ao da entrada
    result = fahrenheit_to_celsius(inf_input)
    assert math.isinf(result), "Expected result to be infinite"
    # Verifica se o sinal do resultado é o mesmo da entrada
    assert math.copysign(1.0, result) == expected_sign, \
        f"Expected sign {expected_sign}, got {math.copysign(1.0, result)}"