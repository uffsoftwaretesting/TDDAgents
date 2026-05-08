from typing import Callable
from numbers import Real

def solve(f: Callable[[float, float], float], t0: float, tf: float, y0: float, n: int) -> float:
    """
    Approximate the solution of y' = f(t, y) on [t0, tf] using the explicit Euler method.
    Compute the uniform step size and perform iteration over each step.
    """
    # Input validations
    if not callable(f):
        raise TypeError("f must be a callable accepting (float, float) and returning float")
    if not isinstance(t0, float) or not isinstance(tf, float) or not isinstance(y0, float):
        raise TypeError("t0, tf and y0 must be floats")
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n <= 0:
        raise ValueError("n must be a positive integer")
    if tf <= t0:
        raise ValueError("tf must be greater than t0")

    # Compute uniform step size h
    h = (tf - t0) / n
    y = y0
    t = t0
    # Perform n explicit Euler steps
    for _ in range(n):
        # Evaluate derivative and check return type
        f_val = f(t, y)
        if not isinstance(f_val, Real):
            raise TypeError("f must return a float")
        f_val = float(f_val)
        y = y + h * f_val
        t = t + h
    return y
