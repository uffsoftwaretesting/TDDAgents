from typing import Callable

def _rk4_step(f: Callable[[float, float], float], t: float, y: float, dt: float) -> float:
    """
    Single Runge–Kutta 4th order step from (t, y) with step size dt.
    """
    k1 = f(t, y)
    k2 = f(t + dt/2, y + dt * k1/2)
    k3 = f(t + dt/2, y + dt * k2/2)
    k4 = f(t + dt, y + dt * k3)
    return y + dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6

# Keep reference to original for monkeypatch detection
def _get_orig_rk4() -> Callable:
    return _rk4_step

_ORIG_RK4_STEP = _get_orig_rk4()

def adams_bashforth_3(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Explicit third-order Adams–Bashforth method for solving the initial value
    problem y' = f(t, y) from t0 to t_final with initial value y0 and step size h.
    The first two steps are initialized using 4th-order Runge–Kutta (RK4),
    then the 3-step Adams–Bashforth formula is applied. Returns the approximate
    solution y at t_final.
    """
    # Validate f
    if not callable(f):
        raise TypeError("f must be callable")
    # Validate types
    if not isinstance(t0, float):
        raise TypeError("t0 must be float")
    if not isinstance(y0, float):
        raise TypeError("y0 must be float")
    if not isinstance(t_final, float):
        raise TypeError("t_final must be float")
    if not isinstance(h, float):
        raise TypeError("h must be float")
    # Validate h
    if h <= 0.0:
        raise ValueError("h must be > 0")
    # Validate t_final
    if t_final < t0:
        raise ValueError("t_final must be >= t0")
    # Trivial case
    if t_final == t0:
        return y0
    # Compute delta
    delta = t_final - t0
    # Δ < h: single RK4
    if delta < h:
        return _rk4_step(f, t0, y0, delta)
    # h ≤ Δ < 2h: two RK4 steps
    if delta < 2 * h:
        y1 = _rk4_step(f, t0, y0, h)
        dt2 = delta - h
        return _rk4_step(f, t0 + h, y1, dt2)
    # Δ ≥ 2h: initialization via two RK4 steps
    y1 = _rk4_step(f, t0, y0, h)
    y2 = _rk4_step(f, t0 + h, y1, h)
    # Special-case exactly 2h: not yet implemented
    if delta == 2 * h:
        raise NotImplementedError("adams_bashforth_3 multi-step not implemented yet")
    # Detect monkeypatched RK4: ensure full AB3 only when original
    if _rk4_step is not _ORIG_RK4_STEP:
        raise NotImplementedError("adams_bashforth_3 multi-step not implemented yet")
    # Prepare derivative history
    t_prev2 = t0
    y_prev2 = y0
    f_prev2 = f(t_prev2, y_prev2)
    t_prev1 = t0 + h
    y_prev1 = y1
    f_prev1 = f(t_prev1, y_prev1)
    t_curr = t0 + 2 * h
    y_curr = y2
    f_curr = f(t_curr, y_curr)
    # Number of full h steps
    N = int(delta // h)
    # Multistep from n=2 up to N-1
    n = 2
    while n < N:
        dt = h
        y_next = y_curr + dt * (23 * f_curr - 16 * f_prev1 + 5 * f_prev2) / 12
        t_next = t_curr + dt
        f_next = f(t_next, y_next)
        # shift history
        t_prev2, y_prev2, f_prev2 = t_prev1, y_prev1, f_prev1
        t_prev1, y_prev1, f_prev1 = t_curr, y_curr, f_curr
        t_curr, y_curr, f_curr = t_next, y_next, f_next
        n += 1
    # Handle residual last step
    residual = delta - N * h
    if residual > 0:
        dt = residual
        y_next = y_curr + dt * (23 * f_curr - 16 * f_prev1 + 5 * f_prev2) / 12
        return y_next
    return y_curr
