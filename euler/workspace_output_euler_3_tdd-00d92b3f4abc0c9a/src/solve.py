from typing import Callable, Union
import inspect

def solve(f: Callable[[float, float], float],
          t0: Union[float, int],
          tf: Union[float, int],
          y0: Union[float, int],
          n: int) -> float:
    """
    Perform explicit Euler integration of y'=f(t,y) from t0 to tf using n steps.

    If t0 == tf, returns y0 converted to float immediately.
    """
    # Validate f is callable
    if not callable(f):
        raise TypeError("f must be a callable accepting (t: float, y: float) and returning float")
    # Validate f signature accepts exactly two parameters
    try:
        sig = inspect.signature(f)
    except (ValueError, TypeError):
        raise TypeError("f must be a callable accepting (t: float, y: float) and returning float")
    if len(sig.parameters) != 2:
        raise TypeError("f must be a callable accepting (t: float, y: float) and returning float")
    # Validate t0, tf, y0 are numeric
    if not isinstance(t0, (int, float)) or not isinstance(tf, (int, float)) or not isinstance(y0, (int, float)):
        raise TypeError("t0, tf, y0 must be numeric (float or int)")
    # Validate n is integer
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    # Validate n is positive
    if n <= 0:
        raise ValueError("n must be a positive integer")
    # Immediate return when no interval
    if t0 == tf:
        return float(y0)

    # Basic explicit Euler integration
    h = (tf - t0) / n
    t = float(t0)
    y = float(y0)
    for _ in range(n):
        y = y + h * f(t, y)
        t = t + h
    return y