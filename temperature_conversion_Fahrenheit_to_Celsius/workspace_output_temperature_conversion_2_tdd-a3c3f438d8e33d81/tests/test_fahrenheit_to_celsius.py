import math
import pytest

from src.converter import fahrenheit_to_celsius


def test_normal_conversion_zero():
    # 32°F é 0°C
    assert fahrenheit_to_celsius(32) == pytest.approx(0.0)


def test_normal_conversion_positive():
    # 212°F é 100°C
    assert fahrenheit_to_celsius(212) == pytest.approx(100.0)


def test_normal_conversion_negative():
    # -40°F é -40°C
    assert fahrenheit_to_celsius(-40) == pytest.approx(-40.0)


def test_nan_input_returns_nan():
    # Se input é NaN, deve retornar NaN
    result = fahrenheit_to_celsius(float('nan'))
    assert math.isnan(result)


def test_infinite_positive_returns_inf():
    # Se input é +inf, deve retornar +inf
    result = fahrenheit_to_celsius(float('inf'))
    assert math.isinf(result) and result > 0


def test_infinite_negative_returns_neg_inf():
    # Se input é -inf, deve retornar -inf
    result = fahrenheit_to_celsius(float('-inf'))
    assert math.isinf(result) and result < 0

@pytest.mark.parametrize("invalid_value", ["string", None, [], {}, object()])
def test_invalid_type_raises_type_error(invalid_value):
    # Entradas não numéricas devem levantar TypeError
    with pytest.raises(TypeError):
        fahrenheit_to_celsius(invalid_value)
