import pytest
from temperature_converter.converter import fahrenheit_to_celsius

@ pytest.mark.parametrize("fahrenheit, expected_celsius", [
    (32, 0.0),
    (212, 100.0),
    (-40, -40.0),
])
def test_typical_temperature_conversions(fahrenheit, expected_celsius):
    """
    Casos clássicos de conversão:
    - 32°F → 0.0°C
    - 212°F → 100.0°C
    - -40°F → -40.0°C
    """
    result = fahrenheit_to_celsius(fahrenheit)
    assert isinstance(result, float), "Expected result to be float"
    assert result == expected_celsius, (
        f"Esperado {expected_celsius}°C para entrada {fahrenheit}°F, got {result}°C"
    )