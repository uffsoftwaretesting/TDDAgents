import math
from typing import Union

def fahrenheit_to_celsius(temperature: Union[int, float]) -> float:
    """
    Converte temperatura de Fahrenheit para Celsius.

    Parâmetros:
        temperature (int | float): valor em Fahrenheit.

    Retorna:
        float: valor em Celsius, ou NaN/inf conforme regras.

    Exceções:
        TypeError: se o tipo de temperature não for int ou float.
    """
    # Validação de tipo
    if not isinstance(temperature, (int, float)):
        raise TypeError(f"Invalid type: {type(temperature).__name__}")

    temp = float(temperature)
    # Valores especiais
    if math.isnan(temp):
        return float('nan')
    if math.isinf(temp):
        return temp

    # Conversão para Celsius
    return (temp - 32) * 5.0 / 9.0
