from typing import Callable


def rk4_classico(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    """Método clássico de Runge-Kutta de 4ª ordem para equações diferenciais ordinárias escalares.

    Parameters
    ----------
    f : Callable[[float, float], float]
        Função derivada f(t, y) que retorna a taxa de variação.
    t0 : float
        Tempo inicial.
    y0 : float
        Valor inicial de y em t0.
    t_final : float
        Tempo final de integração. Deve ser maior que t0.
    h : float
        Passo de integração. Deve ser maior que zero.

    Returns
    -------
    float
        Valor aproximado de y(t_final).

    Raises
    ------
    TypeError
        Se f não for invocável ou se algum parâmetro não for float.
    ValueError
        Se h <= 0 ou t_final <= t0.

    Notes
    -----
    1. Calcula o número de passos completos n = floor((t_final - t0)/h).
    2. Ajusta o último passo h_last para atingir exatamente t_final.
    3. Executa n passos com tamanho h e um passo final de tamanho h_last (se > 0).
    """
    # Tipo de f
    if not callable(f):
        raise TypeError("f must be callable")
    # Tipos dos parâmetros escalares
    if not isinstance(t0, float):
        raise TypeError("t0 must be float")
    if not isinstance(y0, float):
        raise TypeError("y0 must be float")
    if not isinstance(t_final, float):
        raise TypeError("t_final must be float")
    if not isinstance(h, float):
        raise TypeError("h must be float")
    # Valores de h e t_final
    if h <= 0:
        raise ValueError("Passo h deve ser maior que zero")
    if t_final <= t0:
        raise ValueError("t_final deve ser maior que t0")

    total_interval = t_final - t0
    # Número de passos completos
    n_steps = int(total_interval // h)
    # Ajuste do último passo
    h_last = total_interval - n_steps * h

    t = t0
    y = y0
    f_local = f
    # Passos completos
    for _ in range(n_steps):
        k1 = f_local(t, y)
        k2 = f_local(t + h/2.0, y + h/2.0 * k1)
        k3 = f_local(t + h/2.0, y + h/2.0 * k2)
        k4 = f_local(t + h, y + h * k3)
        y = y + (h * (k1 + 2.0*k2 + 2.0*k3 + k4) / 6.0)
        t = t + h
    # Passo final ajustado
    if h_last > 0.0:
        h_n = h_last
        k1 = f_local(t, y)
        k2 = f_local(t + h_n/2.0, y + h_n/2.0 * k1)
        k3 = f_local(t + h_n/2.0, y + h_n/2.0 * k2)
        k4 = f_local(t + h_n, y + h_n * k3)
        y = y + (h_n * (k1 + 2.0*k2 + 2.0*k3 + k4) / 6.0)
        t = t + h_n
    return y
