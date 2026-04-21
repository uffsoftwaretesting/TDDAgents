import sys
from typing import Callable, Optional


def _trapezio_composto(f: Callable[[float], float], a: float, b: float, N: int) -> float:
    """
    Calcula a integral de f no intervalo [a, b] pela regra do trapézio composta com N subintervalos.
    """
    h = (b - a) / N
    soma = 0.5 * (f(a) + f(b))
    for i in range(1, N):
        x = a + i * h
        soma += f(x)
    return h * soma


def integracao_trapezio(
    f: Callable[[float], float],
    a: float,
    b: float,
    N: Optional[int] = None,
    tol: Optional[float] = None
) -> float:
    """
    Aproxima a integral de f no intervalo [a, b] pela Regra do Trapézio.

    Retorna 0.0 se a == b.
    Validações de tipos e escolha de modo (composto via N ou adaptativo via tol).
    """
    # Verifica se f é chamável
    if not callable(f):
        raise TypeError("f deve ser uma função Callable[[float], float]")
    # Verifica tipos de a e b
    if not isinstance(a, float) or not isinstance(b, float):
        raise TypeError("a e b devem ser floats")
    # Verifica ordenação de limites
    if a > b:
        raise ValueError("a deve ser menor que b")
    # Caso degenerate: mesma fronteira
    if a == b:
        return 0.0
    # Seleção de modo de integração
    if tol is None:
        # Modo composto via N
        if N is None:
            raise ValueError("É preciso fornecer N ou tol")
        if not isinstance(N, int) or N <= 0:
            raise ValueError("N deve ser int positivo")
        return _trapezio_composto(f, a, b, N)
    else:
        # Modo adaptativo via tol
        if not isinstance(tol, float) or tol <= 0:
            raise ValueError("tol deve ser float positivo")
        max_iter = 20
        # checagem de tolerância mínima
        if tol < sys.float_info.epsilon:
            raise ValueError(f"Não convergiu em até {max_iter} iterações")
        n_prev = 1
        T_prev = _trapezio_composto(f, a, b, n_prev)
        for _ in range(1, max_iter + 1):
            n_curr = n_prev * 2
            T_curr = _trapezio_composto(f, a, b, n_curr)
            erro_est = abs(T_curr - T_prev) / 3.0
            T_corr = T_curr + (T_curr - T_prev) / 3.0
            if erro_est <= tol:
                return T_corr
            T_prev = T_curr
            n_prev = n_curr
        raise ValueError(f"Não convergiu em até {max_iter} iterações")
