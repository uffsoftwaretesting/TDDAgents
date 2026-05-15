"""
Módulo para implementação do método clássico de Runge-Kutta de 4ª ordem (RK4).
"""
from typing import Callable


def rk4_classico(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """
    Resolve uma EDO de primeira ordem y' = f(t, y) usando o método RK4 clássico de 4ª ordem.

    Parâmetros:
        f (Callable[[float, float], float]): função derivativa que recebe (t, y) e retorna dy/dt.
        t0 (float): tempo inicial.
        y0 (float): valor inicial de y em t0.
        t_final (float): tempo final desejado (deve ser >= t0).
        h (float): passo de integração (deve ser > 0).

    Algoritmo:
        Em cada passo de tamanho dt (ajustado no último passo):
            k1 = f(t, y)
            k2 = f(t + dt/2, y + dt*k1/2)
            k3 = f(t + dt/2, y + dt*k2/2)
            k4 = f(t + dt,   y + dt*k3)
            y = y + (dt/6) * (k1 + 2*k2 + 2*k3 + k4)
            t = t + dt

    Retorna:
        float: estimativa de y(t_final).

    Lança:
        TypeError: se f não for chamável ou se f retornar tipo não-float.
        ValueError: se t_final < t0 ou h <= 0.
    """
    # Validações básicas
    if not callable(f):
        raise TypeError("f must be callable")
    if t_final < t0:
        raise ValueError("t_final must be >= t0")
    if h <= 0:
        raise ValueError("h must be > 0")
    # Retorno imediato se não há intervalo a percorrer
    if t_final == t0:
        return y0

    t = t0
    y = y0
    # Iteração com passo adaptado para não ultrapassar t_final
    while t < t_final:
        dt = h
        # Ajusta último passo
        if t + dt > t_final:
            dt = t_final - t
        # Cálculo dos k
        k1 = f(t, y)
        if not isinstance(k1, float):
            raise TypeError("f must return float")
        k2 = f(t + dt / 2, y + dt * k1 / 2)
        if not isinstance(k2, float):
            raise TypeError("f must return float")
        k3 = f(t + dt / 2, y + dt * k2 / 2)
        if not isinstance(k3, float):
            raise TypeError("f must return float")
        k4 = f(t + dt, y + dt * k3)
        if not isinstance(k4, float):
            raise TypeError("f must return float")
        # Atualiza solução e tempo
        y = y + (dt / 6) * (k1 + 2 * k2 + 2 * k3 + k4)
        t = t + dt
    return y
