import pytest
import numpy as np
from diferencas_finitas_bvp.solver import _solve_system


def test_solve_system_simple_1x1():
    """
    Testa um sistema 1x1: [2] u = [4] deve resultar em u = [2].
    """
    A = np.array([[2.0]], dtype=float)
    RHS = np.array([4.0], dtype=float)
    sol = _solve_system(A, RHS)
    assert isinstance(sol, np.ndarray), "Deve retornar numpy.ndarray"
    assert sol.shape == RHS.shape, f"Shape da solução {sol.shape} deve ser igual a {RHS.shape}"
    assert sol[0] == pytest.approx(2.0), f"Solução esperada 2.0, obtida {sol[0]}"


def test_solve_system_identity_matrix():
    """
    Testa solução de sistema com matriz identidade de diferentes tamanhos.
    """
    for size in [1, 3, 5]:
        A = np.eye(size, dtype=float)
        RHS = np.arange(1, size + 1, dtype=float)
        sol = _solve_system(A, RHS)
        assert np.allclose(sol, RHS), f"Para identidade, solução deve ser igual a RHS; obtido {sol}"


def test_solve_system_singular_matrix_raises():
    """
    Matriz singular (linhas dependentes linearmente) deve causar RuntimeError.
    """
    A = np.array([[1.0, 2.0], [2.0, 4.0]], dtype=float)
    RHS = np.array([3.0, 6.0], dtype=float)
    with pytest.raises(RuntimeError):
        _solve_system(A, RHS)
