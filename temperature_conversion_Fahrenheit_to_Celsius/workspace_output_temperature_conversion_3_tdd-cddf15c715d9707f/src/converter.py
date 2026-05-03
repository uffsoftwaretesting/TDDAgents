# src/converter.py
import math

def fahrenheit_to_celsius(fahrenheit):
    """
    Convert a temperature from Fahrenheit to Celsius.

    Raises TypeError for invalid types, propagates NaN and infinities, and
    applies the conversion formula for finite values.
    """
    if not isinstance(fahrenheit, (int, float)):
        raise TypeError("fahrenheit must be int or float")
    # Propagate NaN
    if math.isnan(fahrenheit):
        return float('nan')
    # Propagate infinities
    if math.isinf(fahrenheit):
        return fahrenheit
    # Perform conversion
    return (fahrenheit - 32) * 5.0 / 9.0
