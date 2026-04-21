from typing import Callable

def derivada_diferenca_central(f: Callable[[float], float], x: float, h: float) -> float:
    """
    Aproxima a derivada de uma função real em um ponto usando o método das diferenças finitas centrais (ordem O(h^2)).

    Parameters
    ----------
    f : Callable[[float], float]
        Função matemática pura mapeando float para float.
    x : float
        Ponto de avaliação da derivada.
    h : float
        Passo de perturbação (deve ser maior que zero).

    Returns
    -------
    float
        Aproximação da derivada de f em x.

    Raises
    ------
    TypeError
        Se f não for callable, ou x/h não forem float.
    ValueError
        Se h for menor ou igual a zero.

    Examples
    --------
    >>> def f(x): return x**2
    >>> derivada_diferenca_central(f, 2.0, 1e-3)
    4.0
    """
    if not callable(f):
        raise TypeError("f deve ser Callable[[float], float]")
    if not isinstance(x, float):
        raise TypeError("x deve ser float")
    if not isinstance(h, float):
        raise TypeError("h deve ser float")
    if h <= 0:
        raise ValueError("h deve ser maior que zero")
    return (f(x + h) - f(x - h)) / (2 * h)