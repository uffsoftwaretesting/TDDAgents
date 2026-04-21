from typing import Callable
import math

def adams_bashforth_2(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_eval: float,
    h: float
) -> float:
    # type checks
    if not callable(f):
        raise TypeError("f must be Callable[[float, float], float]")
    if not isinstance(t0, float):
        raise TypeError("t0 must be float")
    if not isinstance(y0, float):
        raise TypeError("y0 must be float")
    if not isinstance(t_eval, float):
        raise TypeError("t_eval must be float")
    if not isinstance(h, float):
        raise TypeError("h must be float")
    # value checks
    if h <= 0:
        raise ValueError("h must be greater than zero")
    if t_eval < t0:
        raise ValueError("t_eval must be greater than or equal to t0")
    # delta calculation and early return
    delta = t_eval - t0
    if delta == 0.0:
        return y0
    # ajustar número de passos e checar limite
    n_steps = math.ceil(delta / h)
    if n_steps > 1000000:
        raise RuntimeError("too many steps")
    h = delta / n_steps
    # passo de arranque (Euler explícito)
    t_prev = t0
    y_prev = y0
    t_curr = t_prev + h
    y_curr = y_prev + h * f(t_prev, y_prev)
    # esquema Adams-Bashforth de 2 passos
    for _ in range(1, n_steps):
        t_next = t_curr + h
        f_curr = f(t_curr, y_curr)
        f_prev = f(t_prev, y_prev)
        y_next = y_curr + h * (1.5 * f_curr - 0.5 * f_prev)
        t_prev, y_prev = t_curr, y_curr
        t_curr, y_curr = t_next, y_next
    return y_curr
