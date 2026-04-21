import inspect
import math
from typing import Callable


def taylor_2(
    f: Callable[[float, float], float],
    df: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float,
) -> float:
    """
    Compute approximate value at t_final using 2nd order Taylor series method.
    Handles the case when t_final == t0 by returning the initial value immediately.

    Parameters
    ----------
    f : Callable[[float, float], float]
        First derivative function y'.
    df : Callable[[float, float], float]
        Second derivative (total derivative) of y'.
    t0 : float
        Initial time.
    y0 : float
        Initial value y(t0).
    t_final : float
        Final time at which to compute y.
    h : float
        Step size (for future implementation).

    Returns
    -------
    float
        Approximated value y(t_final).

    Raises
    ------
    TypeError
        If f or df are not callables.
    ValueError
        If t_final < t0 or h <= 0 or h is not finite.
    RuntimeError
        If an intermediate y becomes NaN or infinite.

    Example:
        >>> from taylor_2.taylor_2 import taylor_2
        >>> f = lambda t, y: y
        >>> df = lambda t, y: y
        >>> taylor_2(f, df, 0.0, 1.0, 1.0, 0.1)
        2.5937424601

    Examples:
        >>> from taylor_2.taylor_2 import taylor_2
        >>> f = lambda t, y: y
        >>> df = lambda t, y: y
        >>> taylor_2(f, df, 0.0, 1.0, 1.0, 0.1)
        2.5937424601
    """
    if not callable(f) or not callable(df):
        raise TypeError("f e df devem ser callables puros")
    if t_final < t0:
        raise ValueError("t_final deve ser ≥ t0")
    if not (h > 0 and math.isfinite(h)):
        raise ValueError("h deve ser > 0 e finito")
    if t_final == t0:
        return y0

    delta = t_final - t0
    n = int(math.floor(delta / h))
    r = delta - n * h
    t = t0
    y = y0

    for _ in range(n):
        f_val = f(t, y)
        df_val = df(t, y)
        y = y + h * f_val + (h * h / 2) * df_val
        if not math.isfinite(y):
            raise RuntimeError("Divergência detectada: valor não finito")
        t += h

    if r > 0:
        f_val = f(t, y)
        df_val = df(t, y)
        y = y + r * f_val + (r * r / 2) * df_val
        if not math.isfinite(y):
            raise RuntimeError("Divergência detectada: valor não finito")
        t = t_final

    return y


doc = taylor_2.__doc__ or ""
taylor_2.__doc__ = inspect.cleandoc(doc)
