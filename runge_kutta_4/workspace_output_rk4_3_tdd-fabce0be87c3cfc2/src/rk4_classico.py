from typing import Callable

def rk4_classico(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Classical fourth-order Runge-Kutta (RK4) method for solving y' = f(t, y).

    Parameters
    ----------
    f : Callable[[float, float], float]
        Function representing the derivative dy/dt = f(t, y).
    t0 : float
        Initial time.
    y0 : float
        Initial value of y at time t0.
    t_final : float
        Final time for evaluation.
    h : float
        Nominal integration step size.

    Returns
    -------
    float
        Approximation of y at t_final.

    Raises
    ------
    TypeError
        If f is not callable or t0, y0, t_final, h are not floats.
    ValueError
        If h <= 0 or t_final < t0.
    RuntimeError
        If an integration sub-step dt is not positive (<= 0).
    """
    # Validações básicas
    if not callable(f):
        raise TypeError("f deve ser callable")
    if not isinstance(t0, float):
        raise TypeError("t0 deve ser float")
    if not isinstance(y0, float):
        raise TypeError("y0 deve ser float")
    if not isinstance(t_final, float):
        raise TypeError("t_final deve ser float")
    if not isinstance(h, float):
        raise TypeError("h deve ser float")
    if h <= 0:
        raise ValueError("Passo h deve ser positivo")
    if t_final < t0:
        raise ValueError("t_final deve ser maior ou igual a t0")
    # Caso trivial
    if t0 == t_final:
        return y0

    t = t0
    y = y0
    # Loop de integração RK4
    while t < t_final:
        dt = min(h, t_final - t)
        if dt <= 0:
            raise RuntimeError("Passo de integração não positivo")
        k1 = f(t, y)
        k2 = f(t + dt / 2, y + dt * k1 / 2)
        k3 = f(t + dt / 2, y + dt * k2 / 2)
        k4 = f(t + dt, y + dt * k3)
        y = y + (dt / 6) * (k1 + 2 * k2 + 2 * k3 + k4)
        t = t + dt
    return y
