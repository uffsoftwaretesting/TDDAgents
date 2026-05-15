"""
Módulo de integração numérica.
"""
import numpy as np
from typing import Callable


def integracao_simpson_1_3(
    f: Callable[[float], float],
    a: float,
    b: float,
    N: int,
    use_numpy: bool = False
) -> float:
    """
    Aproxima o valor de uma integral definida usando a Regra de Simpson 1/3
    Composta.

    Parâmetros:
    - f: função a ser integrada, deve receber e retornar float.
    - a: limite inferior do intervalo (float).
    - b: limite superior do intervalo (float).
    - N: número de subintervalos (deve ser par e maior que zero).
    - use_numpy: flag booleano, se True usa NumPy para cálculos vetorizados.

    Retorna:
    - float: valor aproximado da integral de f em [a, b].

    Exceções:
    - TypeError: se f não for callable, ou a/b não forem numéricos.
    - ValueError: se N não for inteiro par e maior que zero.
    """
    # Validações de entrada
    if not callable(f):
        raise TypeError("f deve ser callable")
    if not isinstance(a, (int, float)):
        raise TypeError("a deve ser numérico")
    if not isinstance(b, (int, float)):
        raise TypeError("b deve ser numérico")
    if not isinstance(N, int) or N <= 0:
        raise ValueError("N deve ser inteiro par e maior que zero.")
    if N % 2 != 0:
        raise ValueError("N deve ser par.")

    # Caso de borda: limites iguais retornam zero de área
    if a == b:
        return 0.0

    # Cálculo do passo
    h = (b - a) / N

    if use_numpy:
        # Geração de pontos igualmente espaçados
        x = np.linspace(a, b, N + 1)
        y = f(x)
        # Soma dos termos para Regra de Simpson 1/3 composta
        total = y[0] + y[-1]
        total += 4 * np.sum(y[1:-1:2])
        total += 2 * np.sum(y[2:-1:2])
        result = (h / 3) * total
        return float(result)

    # Regra de Simpson 1/3 composta
    total = f(a) + f(b)
    # Soma dos termos dentro do intervalo
    for i in range(1, N):
        x_i = a + i * h
        coef = 4 if i % 2 != 0 else 2
        total += coef * f(x_i)
    result = total * (h / 3)
    return result
