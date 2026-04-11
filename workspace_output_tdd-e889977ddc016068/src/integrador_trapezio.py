"""
Módulo integrador por regra composta do trapézio.

Este módulo implementa a regra composta do trapézio para aproximação de integrais definidas,
oferecendo duas implementações:
- solve: abordagem sequencial.
- solve_vectorized: abordagem vetorizada usando numpy.

A vetorização permite avaliar funções que aceitam numpy.ndarray como entrada.
"""
import numpy as np


def solve(f, a, b, n):
    """
    Aproxima a integral definida de f no intervalo [a, b] usando regra composta do trapézio.

    Parâmetros:
        f (callable): função de um argumento float.
        a (float): limite inferior.
        b (float): limite superior.
        n (int): número de subintervalos (n > 0).

    Retorna:
        float: aproximação da integral.

    Nota: implementa regra composta do trapézio.
    """
    # Validação de f
    if not callable(f):
        raise ValueError("f must be callable")
    # Validação de n
    if not isinstance(n, int) or n <= 0:
        raise ValueError("n must be an integer greater than 0")
    # Validação do intervalo
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        raise ValueError("Limits a and b must be numeric")
    if b < a:
        raise ValueError("Upper limit b must be greater than or equal to a")
    # Caso de intervalo nulo: não invoca f
    if b == a:
        return 0.0
    
    h = (b - a) / n
    x = np.linspace(a, b, n + 1)  # pontos sem acúmulo de erro
    
    fa = f(float(x[0]))
    fb = f(float(x[-1]))
    
    sum_internal = sum(f(float(x[i])) for i in range(1, n))
    
    return (h / 2) * (fa + 2 * sum_internal + fb)


def solve_vectorized(f, a, b, n):
    """
    Aproxima a integral definida de f no intervalo [a, b] usando regra composta do trapézio
    de forma vetorizada com numpy.

    Parâmetros:
        f (callable): função que aceita float ou numpy.ndarray e retorna float ou numpy.ndarray.
        a (float): limite inferior.
        b (float): limite superior.
        n (int): número de subintervalos (n > 0).

    Retorna:
        float: aproximação da integral.
    """
    # Validação de f
    if not callable(f):
        raise ValueError("f must be callable")
    # Validação de n
    if not isinstance(n, int) or n <= 0:
        raise ValueError("n must be an integer greater than 0")
    # Validação do intervalo
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        raise ValueError("Limits a and b must be numeric")
    if b < a:
        raise ValueError("Upper limit b must be greater than or equal to a")
    # Caso de intervalo nulo
    if b == a:
        return 0.0
    # Passo de malha
    h = (b - a) / n
    # Cria pontos igualmente espaçados
    x = np.linspace(a, b, n + 1)
    # Avalia f de forma vetorizada ou escalares via vectorize
    try:
        y = f(x)
        y = np.array(y, dtype=float)
    except TypeError:
        # f não aceita array, aplica np.vectorize
        vec_f = np.vectorize(f, otypes=[float])
        y = vec_f(x)
    # Soma interna (todos os pontos exceto extremos)
    if n > 1:
        sum_internal = np.sum(y[1:-1])
    else:
        sum_internal = 0.0
    # Fórmula composta do trapézio vetorizada
    result = (h / 2) * (y[0] + 2 * sum_internal + y[-1])
    return float(result)