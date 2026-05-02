from typing import Callable

def rk4_classico(f: Callable[[float, float], float], t0: float, y0: float, t_final: float, h: float) -> float:
    """
    Resolve ODE dy/dt = f(t, y) desde o tempo inicial t0 até t_final usando o método clássico de Runge-Kutta de 4ª ordem.

    :param f: Callable[[float, float], float] função que calcula dy/dt dado (t, y).
    :param t0: float tempo inicial.
    :param y0: float valor inicial de y em t0.
    :param t_final: float tempo final para estimativa de y, deve ser >= t0.
    :param h: float passo de integração, deve ser > 0.
    :return: float estimativa de y em t_final.
    :raises TypeError: se algum parâmetro não tiver o tipo esperado.
    :raises ValueError: se h <= 0 ou t_final < t0.
    """
    # Validações de tipo e valor
    if not callable(f):
        raise TypeError("f must be callable")
    for name, param in [("t0", t0), ("y0", y0), ("t_final", t_final), ("h", h)]:
        if not isinstance(param, float):
            raise TypeError(f"{name} must be float")
    if h <= 0.0:
        raise ValueError("h must be > 0")
    if t_final < t0:
        raise ValueError("t_final must be >= t0")

    y = y0
    t = t0
    total = t_final - t0
    # número de passos completos de tamanho h
    N = int(total // h)
    for _ in range(N):
        dt = h
        k1 = f(t, y)
        k2 = f(t + dt/2, y + dt*k1/2)
        k3 = f(t + dt/2, y + dt*k2/2)
        k4 = f(t + dt, y + dt*k3)
        y = y + (dt/6) * (k1 + 2*k2 + 2*k3 + k4)
        t = t + dt
    # passo final, se houver resto
    dt_final = t_final - t
    if dt_final > 0.0:
        dt = dt_final
        k1 = f(t, y)
        k2 = f(t + dt/2, y + dt*k1/2)
        k3 = f(t + dt/2, y + dt*k2/2)
        k4 = f(t + dt, y + dt*k3)
        y = y + (dt/6) * (k1 + 2*k2 + 2*k3 + k4)
    return y
