from typing import Callable


def integracao_simpson_1_3(
    f: Callable[[float], float],
    a: float,
    b: float,
    N: int
) -> float:
    """
    Approxima a integral definida de f em [a, b] usando a regra composta de
    Simpson 1/3.

    Parâmetros:
        f: Função integranda, chamável que recebe float e retorna float.
        a: Limite inferior de integração (int ou float).
        b: Limite superior de integração (int ou float).
        N: Número de subintervalos (int par positivo).

    Retorna:
        Aproximação da integral como float.

    Exceções:
        TypeError: se f não for chamável, ou a/b não forem numéricos.
        ValueError: se N não for inteiro, não for positivo nem par.
    """
    # Validação de parâmetros
    if not callable(f):
        raise TypeError("f must be callable")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("a and b must be numbers")
    if not isinstance(N, int):
        raise ValueError("N must be an integer")
    if N <= 0 or N % 2 != 0:
        raise ValueError("N must be positive and even")

    if a == b:
        return 0.0

    if a > b:
        return -integracao_simpson_1_3(f, b, a, N)

    h = (b - a) / N
    total = f(a) + f(b)
    for i in range(1, N):
        x = a + i * h
        if i % 2 == 0:
            total += 2 * f(x)
        else:
            total += 4 * f(x)
    return total * (h / 3)
