"""
Módulo de montagem da matriz e do vetor RHS.
"""
import numpy as np


def _generate_mesh(a: float, b: float, N: int):
    """
    Gera o passo h e o vetor de nós x para o intervalo [a, b] com N nós internos.
    """
    # Compute mesh spacing
    h = (b - a) / (N + 1)
    # Generate mesh nodes
    x = np.linspace(a, b, N + 2)
    return h, x



def _assemble_system(x: np.ndarray, h: float, bc: dict, f):
    """
    Monta a matriz A e o vetor RHS para o sistema de diferenças finitas.

    Parâmetros:
        x: array de nós da malha (tamanho N+2)
        h: espaçamento da malha
        bc: condições de contorno {'u_a': float, 'u_b': float}
        f: função fonte vetorizada

    Retorna:
        A: matriz tridiagonal de shape (N, N)
        RHS: vetor de comprimento N
    """
    # Número de nós internos
    N = x.shape[0] - 2
    # Ainda não implementado para N=1
    if N == 1:
        raise NotImplementedError
    # Montar matriz tridiagonal A
    # Diagonal principal com 2
    A = 2.0 * np.eye(N)
    # Sub e superdiagonais com -1
    A += -1.0 * np.eye(N, k=1)
    A += -1.0 * np.eye(N, k=-1)
    # Montar vetor RHS
    # Avaliar f nos nós internos
    x_int = x[1:-1]
    f_vals = f(x_int)
    # Checar se retornou valores numéricos finitos
    if not isinstance(f_vals, np.ndarray):
        raise ValueError("f must return numpy.ndarray")
    if not np.isfinite(f_vals).all():
        raise ValueError("f returned NaN or Inf")
    RHS = (h ** 2) * f_vals
    # Ajustar pelas condições de contorno
    RHS[0] += bc['u_a']
    RHS[-1] += bc['u_b']
    return A, RHS