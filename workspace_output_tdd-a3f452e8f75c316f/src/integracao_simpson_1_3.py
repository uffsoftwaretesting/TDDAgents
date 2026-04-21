from typing import Callable, Union

try:
    import numpy as np
except ImportError:
    np = None

def integracao_simpson_1_3(f: Callable[[float], float], a: Union[float, int], b: Union[float, int], N: int) -> float:
    """
    Aproxima a integral de f no intervalo [a, b] usando a Regra de Simpson 1/3 Composta.

    Parâmetros:
        f (Callable[[float], float]): Função integranda. Recebe um float (ou array de floats quando NumPy está disponível) e retorna float.
        a (float | int): Limite inferior da integração. Valores inteiros são convertidos para float.
        b (float | int): Limite superior da integração. Valores inteiros são convertidos para float.
        N (int): Número de subintervalos, deve ser inteiro, par e positivo.

    Retorna:
        float: Aproximação da integral de f em [a, b].

    Levanta:
        TypeError: se N não for int ou se a/b não forem números (int ou float).
        ValueError: se N não for par e positivo, ou se a > b.

    Exemplos:
        >>> import math
        >>> integracao_simpson_1_3(lambda x: x**2, 0, 1, 2)
        0.3333333333333333
        >>> integracao_simpson_1_3(math.sin, 0, math.pi, 100)
        2.0
    """
    # Validação de tipo de N
    if not isinstance(N, int):
        raise TypeError("N deve ser do tipo int")
    # Validação de valor de N (deve ser par e positivo)
    if N <= 0 or N % 2 != 0:
        raise ValueError("N deve ser um inteiro par positivo")
    # Validação de tipos de a e b
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("Limites de integração devem ser números")
    # Conversão de limites para float
    a = float(a)
    b = float(b)
    # Se limites iguais, retorna 0.0
    if a == b:
        return 0.0
    # Se limite inferior maior que superior, erro
    if a > b:
        raise ValueError("Limite inferior a deve ser menor ou igual ao limite superior b")
    # Passo de integração
    h = (b - a) / N
    if np is not None:
        # Usa numpy para gerar os pontos, mas avalia ponto a ponto para compatibilidade
        x = np.linspace(a, b, N + 1)
        # Avaliação ponto a ponto para suportar funções não vetorizadas (ex: math.sin)
        y = np.array([f(xi) for xi in x], dtype=float)
        sum_odd = np.sum(y[1::2])
        sum_even = np.sum(y[2:-1:2])
        integral = (h / 3) * (y[0] + y[-1] + 4 * sum_odd + 2 * sum_even)
        return float(integral)
    else:
        # Implementação pure Python
        sum_odd = 0.0
        sum_even = 0.0
        for i in range(1, N):
            x_i = a + i * h
            fx = f(x_i)
            if i % 2 == 0:
                sum_even += fx
            else:
                sum_odd += fx
        integral = (h / 3) * (f(a) + f(b) + 4 * sum_odd + 2 * sum_even)
        return integral
