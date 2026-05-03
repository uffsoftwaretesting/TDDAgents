"""Integration module implementing the composite Simpson's 1/3 rule.

This module provides the `integracao_simpson_1_3` function to approximate
the definite integral of a callable function over a closed interval
[a, b] using the composite Simpson's 1/3 rule.
"""

from typing import Callable


def integracao_simpson_1_3(
    f: Callable[[float], float],
    a: float,
    b: float,
    N: int
) -> float:
    """Approximate the definite integral of a function over [a, b]
    using the composite Simpson's 1/3 rule.

    Parameters
    ----------
    f : Callable[[float], float]
        The integrand function that takes a float and returns a float.
    a : float
        Lower limit of integration.
    b : float
        Upper limit of integration.
    N : int
        Number of subintervals (must be a positive even integer).

    Returns
    -------
    float
        Approximation of the integral.

    Raises
    ------
    TypeError
        If `f` is not callable, `a` or `b` are not numeric types,
        or `N` is not an integer.
    ValueError
        If `N` is not a positive even integer.
    """
    # Basic validations
    if not callable(f):
        raise TypeError(
            "f deve ser uma função callable que recebe float e retorna float"
        )
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("a e b devem ser valores numéricos (float ou int)")
    if not isinstance(N, int):
        raise TypeError("N deve ser um inteiro par e positivo")
    if N <= 0 or N % 2 != 0:
        raise ValueError("N deve ser um inteiro par e maior que zero")

    # Convert limits to float
    a = float(a)
    b = float(b)

    # Step size
    h = (b - a) / N

    # Initial sum with endpoints
    total = f(a) + f(b)

    # Sum intermediate terms
    for i in range(1, N):
        x_i = a + i * h
        coef = 4 if i % 2 != 0 else 2
        total += coef * f(x_i)

    # Final approximation
    return total * (h / 3)