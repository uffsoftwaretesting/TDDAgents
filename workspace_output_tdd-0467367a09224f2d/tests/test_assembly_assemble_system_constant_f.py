import numpy as np
import pytest
from diferencas_finitas_bvp.assembly import _generate_mesh, _assemble_system


def test_assemble_system_constant_f_correct_matrix_and_rhs():
    """
    Quando f retorna uma constante c, verifica:
    - A é tridiagonal com 2 na diagonal principal e -1 nas off-diagonais
    - RHS = h^2*c para todos índices, ajustado com u_a e u_b
    """
    # Domínio e malha
    a, b, N = 0.0, 1.0, 4
    h, x = _generate_mesh(a, b, N)
    # Fonte constante
    c = 3.0
    def const_f(x_arr: np.ndarray) -> np.ndarray:
        return np.full_like(x_arr, c)
    # Condições de contorno
    bc = {'u_a': 2.0, 'u_b': 4.0}
    # Montar sistema
    A, RHS = _assemble_system(x, h, bc, const_f)

    # Verificar shapes
    assert isinstance(A, np.ndarray)
    assert isinstance(RHS, np.ndarray)
    assert A.shape == (N, N)
    assert RHS.shape == (N,)

    # Verificar matriz A
    # Diagonal principal = 2
    assert np.allclose(np.diag(A), 2.0)
    # Sub- e superdiagonais = -1
    assert np.allclose(np.diag(A, k=1), -1.0)
    assert np.allclose(np.diag(A, k=-1), -1.0)
    # Fora da banda tridiagonal deve ser zero
    for i in range(N):
        for j in range(N):
            if abs(i - j) > 1:
                assert A[i, j] == 0.0, f"A[{i},{j}] deve ser 0"

    # Verificar RHS
    expected_base = np.full(N, h**2 * c)
    expected_base[0] += bc['u_a']
    expected_base[-1] += bc['u_b']
    assert np.allclose(RHS, expected_base), f"RHS esperado {expected_base}, obtido {RHS}"
