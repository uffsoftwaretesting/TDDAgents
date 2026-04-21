"""
Resolve o problema de valor de contorno -u''(x) = f(x) via diferenças finitas.

:param f: Função fonte vetorizada, aceita numpy.ndarray e retorna numpy.ndarray.
:param a: Limite esquerdo do domínio.
:param b: Limite direito do domínio.
:param bc: Condições de contorno {'u_a': float, 'u_b': float}.
:param N: Número de nós internos na malha.
:param x_alvo: Ponto de avaliação dentro do intervalo [a, b].
:returns: Aproximação de u(x_alvo).
:raises ValueError: Se parâmetros inválidos ou f retorna NaN/Inf.
:raises RuntimeError: Se falha na montagem ou resolução do sistema.

Examples
--------
>>> import numpy as np
>>> from diferencas_finitas_bvp.core import diferencas_finitas_bvp
>>> f = lambda x: np.ones_like(x)
>>> a, b = 0.0, 1.0
>>> bc = {'u_a': 0.0, 'u_b': 0.0}
>>> N = 10
>>> x_alvo = 0.5
>>> result = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
>>> print(result)
"""
import numpy as np
from typing import Callable, Dict

from diferencas_finitas_bvp.validation import _validate_inputs
from diferencas_finitas_bvp.assembly import _generate_mesh, _assemble_system
from diferencas_finitas_bvp.solver import _solve_system
from diferencas_finitas_bvp.interpolation import _evaluate_at_target


def _build_full_solution(u_int, bc):
    """
    Constrói a solução completa incluindo condições de contorno.

    Parâmetros
    ----------
    u_int : array-like
        Solução interna nos nós internos.
    bc : dict
        Condições de contorno {'u_a': float, 'u_b': float}.

    Returns
    -------
    numpy.ndarray
        Vetor contendo [u_a, *u_int, u_b].
    """
    # Converter u_int para numpy array com dtype float
    u_int_arr = np.array(u_int, dtype=float)
    # Extrair valores de contorno
    u_a = bc['u_a']
    u_b = bc['u_b']
    # Concatenar fronteiras e solução interna
    return np.concatenate(([u_a], u_int_arr, [u_b]))


def diferencas_finitas_bvp(
    f: Callable[[np.ndarray], np.ndarray],
    a: float,
    b: float,
    bc: Dict[str, float],
    N: int,
    x_alvo: float
) -> float:
    """
Resolve o problema de valor de contorno -u''(x) = f(x) via diferenças finitas.

:param f: Função fonte vetorizada, aceita numpy.ndarray e retorna numpy.ndarray.
:param a: Limite esquerdo do domínio.
:param b: Limite direito do domínio.
:param bc: Condições de contorno {'u_a': float, 'u_b': float}.
:param N: Número de nós internos na malha.
:param x_alvo: Ponto de avaliação dentro do intervalo [a, b].
:returns: Aproximação de u(x_alvo).
:raises ValueError: Se parâmetros inválidos ou f retorna NaN/Inf.
:raises RuntimeError: Se falha na montagem ou resolução do sistema.

Examples
--------
>>> import numpy as np
>>> from diferencas_finitas_bvp.core import diferencas_finitas_bvp
>>> f = lambda x: np.ones_like(x)
>>> a, b = 0.0, 1.0
>>> bc = {'u_a': 0.0, 'u_b': 0.0}
>>> N = 10
>>> x_alvo = 0.5
>>> result = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
>>> print(result)
"""
    # Validação de parâmetros
    _validate_inputs(f, a, b, bc, N, x_alvo)
    # Geração da malha
    h, x = _generate_mesh(a, b, N)
    # Montagem do sistema linear
    A, RHS = _assemble_system(x, h, bc, f)
    # Resolução do sistema interno
    u_int = _solve_system(A, RHS)
    # Construção da solução completa
    u = _build_full_solution(u_int, bc)
    # Avaliação no ponto alvo
    return _evaluate_at_target(u, x, x_alvo)
