import math
from typing import Union


def fahrenheit_to_celsius(temperature: Union[int, float]) -> float:
    """
    Converts a temperature from Fahrenheit to Celsius.

    Args:
        temperature (int or float): Temperature in degrees Fahrenheit.

    Returns:
        float: Temperature converted to degrees Celsius.

    Raises:
        TypeError: If temperature is not an int or float.
    """
    # Validação de tipo: apenas int ou float são permitidos
    if not isinstance(temperature, (int, float)):
        raise TypeError("temperature must be an int or float")
    # Tratamento de NaN
    if math.isnan(temperature):
        return float('nan')
    # Tratamento de infinito
    if math.isinf(temperature):
        return math.copysign(float('inf'), temperature)
    # Cálculo para valores finitos
    return (temperature - 32) * 5.0 / 9.0
