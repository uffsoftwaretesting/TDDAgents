from typing import Callable
import math

def rk2_heun(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Implements RK2 Heun method for solving y'=f(t,y) with initial condition (t0, y0).

    Args:
        f: Callable[[float, float], float] computing dy/dt = f(t, y).
        t0: Initial time (float).
        y0: Initial value y(t0) (float).
        t_final: Final time to integrate to (>= t0) (float).
        h: Step size (float > 0).

    Returns:
        Approximate value of y at t_final as float.

    Raises:
        TypeError: if f is not callable or t0, y0, t_final, h are not floats.
        ValueError: if h <= 0 or t_final < t0.
    """
    # Validate f
    if not callable(f):
        raise TypeError("f must be a callable[[float,float], float]")
    # Validate parameter types
    if not isinstance(t0, float) or not isinstance(y0, float) or not isinstance(t_final, float) or not isinstance(h, float):
        raise TypeError("t0, y0, t_final and h must be floats")
    # Validate h
    if h <= 0:
        raise ValueError("h must be greater than zero")
    # Validate t_final
    if t_final < t0:
        raise ValueError("t_final must be greater than or equal to t0")
    # Trivial early return if no integration needed
    if t_final == t0:
        return y0

    # Compute number of full steps and remainder capturing floating residues
    total_interval = t_final - t0
    raw_steps = total_interval / h
    n_full = int(math.floor(raw_steps))
    # two ways to compute remainder
    direct_rem = total_interval - n_full * h
    frac_rem = (raw_steps - n_full) * h
    remainder = direct_rem if direct_rem > frac_rem else frac_rem

    # Build list of step sizes
    steps = []
    if n_full > 0:
        steps.extend([h] * n_full)
    if remainder > 0:
        steps.append(remainder)

    # Initialize
    t = t0
    y = y0

    # Integration loop
    for hi in steps:
        # First slope
        k1_raw = f(t, y)
        k1 = float(k1_raw)
        # Predictor
        y_pred = y + hi * k1
        # Second slope
        k2_raw = f(t + hi, y_pred)
        k2 = float(k2_raw)
        # Corrector
        y = y + (hi / 2.0) * (k1 + k2)
        # Advance time
        t += hi

    return y
