from typing import Callable

def solve(f: Callable[[float, float], float],
          t0: float,
          tf: float,
          y0: float,
          n: int) -> float:
    """
    Solve an ODE y' = f(t, y) from t0 to tf with initial condition y0
    using the explicit Euler method over n steps.

    Args:
        f (Callable[[float, float], float]): Right-hand side of the ODE, f(t, y).
        t0 (float): Initial time.
        tf (float): Final time.
        y0 (float): Initial value y(t0).
        n (int): Number of steps (must be > 0).

    Returns:
        float: Approximation of y(tf).

    Raises:
        TypeError: If f is not callable or t0, tf, y0 are not numeric.
        ValueError: If n is not a positive integer.
        OverflowError: Propagated from f if it overflows.
        Any exception raised by f propagates unchanged.
    """
    if not callable(f):
        raise TypeError("f must be callable")
    if not isinstance(t0, (int, float)) or not isinstance(tf, (int, float)) or not isinstance(y0, (int, float)):
        raise TypeError("t0, tf and y0 must be numeric")
    if not isinstance(n, int) or n <= 0:
        raise ValueError("n must be a positive integer")

    # If the interval is zero-length, return the initial value without calling f
    if tf == t0:
        return float(y0)

    h = (tf - t0) / n
    t = float(t0)
    y = float(y0)
    for _ in range(n):
        y += h * f(t, y)
        t += h
    return y
