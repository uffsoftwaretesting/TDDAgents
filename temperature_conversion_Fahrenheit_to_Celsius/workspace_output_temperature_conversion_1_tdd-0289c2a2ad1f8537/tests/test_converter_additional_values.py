import pytest
from temperature_converter.converter import fahrenheit_to_celsius

@ pytest.mark.parametrize("fahrenheit, expected_celsius", [
    (0, -17.77777777777778),
    (100, 37.77777777777778),
    (98.6, 37.0),
    (45.5, 7.5),
])
def test_additional_temperature_conversions(fahrenheit, expected_celsius):
    """
    Test conversions para valores inteiros diversos e fracionários,
    usando pytest.approx para tolerância numérica.
    """
    result = fahrenheit_to_celsius(fahrenheit)
    assert isinstance(result, float), "Expected result to be float"
    assert result == pytest.approx(expected_celsius), \
        f"Expected approximately {expected_celsius}°C for input {fahrenheit}°F, got {result}°C"