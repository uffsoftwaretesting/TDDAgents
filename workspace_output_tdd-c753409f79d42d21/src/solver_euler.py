"""Explicit Euler solver for scalar ODE initial value problems."""

import math
from typing import Callable


def euler_explicito(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Solve initial value problems for scalar ODEs using the explicit Euler method.

    Parameters:
    f (Callable[[float, float], float]):
        Function defining the ODE derivative dy/dt = f(t, y).
    t0 (float):
        Initial time of the integration interval.
    y0 (float):
        Initial value y(t0) of the solution.
    t_final (float):
        Final time of the integration, must satisfy t_final >= t0.
    h (float):
        Step size for integration, must be positive.

    Returns:
    float:
        Approximate solution y at t_final.

    Raises:
    TypeError:
        If f is not callable or any of t0, y0, t_final, h is not a float.
    ValueError:
        If h <= 0, t_final < t0, or the number of steps (N) is not an integer
        within a tolerance of 1e-8 + 1e-12.
    RuntimeError:
        If the derivative function f raises an exception during evaluation.

    Notes:
    The number of steps N is computed as (t_final - t0) / h and rounded to
    the nearest integer within the specified tolerance to account for
    floating-point errors.
    """
    # Type checks
    if not callable(f):
        raise TypeError("f must be callable")
    if not isinstance(t0, float):
        raise TypeError("t0 must be float")
    if not isinstance(y0, float):
        raise TypeError("y0 must be float")
    if not isinstance(t_final, float):
        raise TypeError("t_final must be float")
    if not isinstance(h, float):
        raise TypeError("h must be float")
    # Domain validations
    if h <= 0:
        raise ValueError("h must be positive")
    if t_final < t0:
        raise ValueError("t_final must be >= t0")
    # Compute number of steps and check integrality within tolerance
    N_float = (t_final - t0) / h
    tol = 1e-8 + 1e-12
    N_round = round(N_float)
    if abs(N_float - N_round) > tol:
        raise ValueError("Number of steps must be integer")
    N = int(N_round)
    # Zero steps: return initial value without calling f
    if N == 0:
        return y0
    # Explicit Euler iteration
    t = t0
    y = y0
    for i in range(N):
        try:
            dy = f(t, y)
        except Exception as e:
            raise RuntimeError(f"Error evaluating derivative at step {i}: {e}")
        y = y + h * dy
        t = t + h
    return y
