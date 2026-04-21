from typing import Callable


def rk2_ponto_medio(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Runge-Kutta 2nd order midpoint method solver.

    Parameters
    ----------
    f : Callable[[float, float], float]
        Function that implements the differential equation dy/dt = f(t, y).
    t0 : float
        Initial time.
    y0 : float
        Initial value at time t0.
    t_final : float
        Final time for integration (must be >= t0).
    h : float
        Step size for integration (must be > 0).

    Returns
    -------
    float
        Approximate value of y at t_final.

    Raises
    ------
    TypeError
        If inputs are of incorrect type or intermediate slopes are not floats.
    ValueError
        If numeric parameters are out of valid range.
    """
    # Validação de parâmetros
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
    if h <= 0:
        raise ValueError("h must be > 0")
    if t_final < t0:
        raise ValueError("t_final must be >= t0")

    # Caso trivial
    if t_final == t0:
        return y0

    # Integração pelo método RK2 ponto-médio
    t = t0
    y = y0
    while t < t_final:
        hi = h if (t + h) <= t_final else (t_final - t)
        # Primeiro estágio
        k1 = f(t, y)
        if not isinstance(k1, float):
            raise TypeError("k1 must be float")
        # Ponto médio
        y_mid = y + (hi / 2.0) * k1
        t_mid = t + (hi / 2.0)
        # Segundo estágio
        k2 = f(t_mid, y_mid)
        if not isinstance(k2, float):
            raise TypeError("k2 must be float")
        # Atualiza solução
        y = y + hi * k2
        t = t + hi
    return y
