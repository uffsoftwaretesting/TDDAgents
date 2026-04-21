import pytest
import numpy as np
from diferencas_finitas_bvp.assembly import _generate_mesh, _assemble_system

# Função fonte dummy que retorna zeros no mesmo formato de x

def dummy_f(x: np.ndarray) -> np.ndarray:
    return np.zeros_like(x)


def test_assemble_system_exists():
    """
    Verifica que _assemble_system existe e é chamável.
    """
    assert callable(_assemble_system), "_assemble_system deve ser uma função"


def test_assemble_system_not_implemented():
    """
    Chamada ao stub deve levantar NotImplementedError.
    """
    # Definindo dados de entrada simples
    x = np.array([0.0, 0.5, 1.0])
    h = 0.5
    bc = {'u_a': 0.0, 'u_b': 1.0}
    with pytest.raises(NotImplementedError):
        _assemble_system(x, h, bc, dummy_f)


def test_assemble_system_correct_dimensions_and_values_for_zero_f():
    """
    Quando f retorna zeros, verifica dimensões e conteúdo de A e RHS:
    - A deve ser tridiagonal (2 na diagonal, -1 nas sub/superdiagonais).
    - RHS deve ser h**2 * f(x_internal) ajustado pelas bcs.
    """
    # Gerar malha para N=3
    h, x = _generate_mesh(0.0, 1.0, 3)
    bc = {'u_a': 1.0, 'u_b': 2.0}
    # Chama a função (espera-se, futuramente, retorno de A e RHS)
    A, RHS = _assemble_system(x, h, bc, dummy_f)

    # Número de nós internos N
    N = 3
    # Verificar shapes
    assert isinstance(A, np.ndarray), "A deve ser numpy.ndarray"
    assert isinstance(RHS, np.ndarray), "RHS deve ser numpy.ndarray"
    assert A.shape == (N, N), f"A deve ter shape ({N}, {N})"
    assert RHS.shape == (N,), f"RHS deve ter shape ({N},)"

    # Verificar conteúdo de A
    # Diagonal principal = 2
    assert np.allclose(np.diag(A), 2.0), "Diagonal de A deve ser 2"
    # Sub- e superdiagonais = -1
    assert np.allclose(np.diag(A, k=1), -1.0), "Superdiagonal de A deve ser -1"
    assert np.allclose(np.diag(A, k=-1), -1.0), "Subdiagonal de A deve ser -1"

    # Verificar conteúdo de RHS
    # f(zero) gera vetor zero -> RHS inicial zeros
    # Ajuste pelas bcs: primeira posição + u_a, última + u_b
    expected_RHS = np.array([1.0, 0.0, 2.0])
    assert np.allclose(RHS, expected_RHS), f"RHS esperado {expected_RHS}, obtido {RHS}"
