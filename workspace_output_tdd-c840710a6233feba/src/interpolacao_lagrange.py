from typing import Sequence, Callable


def interpolacao_lagrange(
    x_nos: Sequence[float],
    f: Callable[[float], float],
    x_alvo: float
) -> float:
    """
    Interpolação via polinômio de Lagrange.

    Parâmetros:
      x_nos: sequência de floats (nós de abscissa distintos, tamanho ≥ 1).
      f: função que recebe float e retorna float.
      x_alvo: ponto de avaliação, float.

    Retorna:
      float: valor interpolado em x_alvo.

    Exceções:
      ValueError: se x_nos vazio ou com nós duplicados.
      TypeError: se tipos de x_nos, x_alvo ou f(x_i) forem incorretos.

    Complexidade: O(n²) operações aritméticas (n = len(x_nos)).
    """
    # Validações iniciais
    try:
        tamanho = len(x_nos)
    except Exception:
        raise ValueError("É necessário pelo menos um nó de abscissa")
    if tamanho < 1:
        raise ValueError("É necessário pelo menos um nó de abscissa")
    # Validação de tipo de x_alvo
    if not isinstance(x_alvo, float):
        raise TypeError("Ponto alvo deve ser float")
    # Todos os nós devem ser float
    for xi in x_nos:
        if not isinstance(xi, float):
            raise TypeError("Todos os nós de abscissa devem ser float")
    # Sem nós duplicados
    if len(set(x_nos)) != tamanho:
        raise ValueError("Nós de abscissa duplicados")
    # Caso único nó
    if tamanho == 1:
        yi = f(x_nos[0])
        if not isinstance(yi, float):
            raise TypeError("Valor de f(x) deve ser float para todos os nós")
        return yi
    # Cálculo de f(x_i) para cada nó (validação de retorno)
    y_values = []
    for xi in x_nos:
        yi = f(xi)
        if not isinstance(yi, float):
            raise TypeError("Valor de f(x) deve ser float para todos os nós")
        y_values.append(yi)
    # Cálculo do polinômio de Lagrange
    resultado = 0.0
    for i in range(tamanho):
        Li = 1.0
        xi = x_nos[i]
        for j in range(tamanho):
            if j != i:
                xj = x_nos[j]
                Li *= (x_alvo - xj) / (xi - xj)
        resultado += y_values[i] * Li
    return resultado
