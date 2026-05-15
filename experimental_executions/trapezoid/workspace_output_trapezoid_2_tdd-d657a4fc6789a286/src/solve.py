from typing import Callable, Union

def solve(f: Callable[[float], float], a: Union[int, float], b: Union[int, float], n: int) -> float:
    """
    Approximates the definite integral of f from a to b using the composite trapezoidal rule.

    Parameters:
        f: A callable that takes a float and returns a float.
        a: The lower limit of integration (int or float).
        b: The upper limit of integration (int or float).
        n: Number of subintervals (must be >= 1).

    Returns:
        Approximation of the integral as a float.

    Raises:
        TypeError: If f is not callable or a or b are not int or float.
        ValueError: If n is not an integer >= 1 or a is not less than b.
    """
    if not callable(f):
        raise TypeError("f must be a callable taking one float argument")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("a and b must be int or float")
    if not isinstance(n, int) or n < 1:
        raise ValueError("n must be an integer ≥ 1")
    if a >= b:
        raise ValueError("a must be less than b")

    h = (b - a) / n
    # sum endpoints
    total = f(a) + f(b)
    # sum interior points with weight 2
    for i in range(1, n):
        x = a + i * h
        total += 2 * f(x)
    return total * (h / 2)
