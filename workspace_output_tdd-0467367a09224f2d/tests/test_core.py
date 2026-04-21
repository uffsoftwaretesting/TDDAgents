import pytest
import numpy as np
from diferencas_finitas_bvp.core import _build_full_solution, diferencas_finitas_bvp


def test_build_full_solution_exists():
    """
    Verifica que _build_full_solution existe e é chamável.
    """
    assert callable(_build_full_solution), "_build_full_solution deve ser uma função ou callable"


def test_build_full_solution_basic_behavior():
    """
    Verifica que _build_full_solution retorna numpy.ndarray com valores corretos para um caso simples.
    """
    u_int = [0.1, 0.2, 0.3]
    bc = {"u_a": 0.0, "u_b": 1.0}
    result = _build_full_solution(u_int, bc)
    assert isinstance(result, np.ndarray), "Resultado deve ser numpy.ndarray"
    expected = np.array([0.0, 0.1, 0.2, 0.3, 1.0])
    assert result.shape == expected.shape, f"Shape esperado {expected.shape}, obtido {result.shape}"
    assert np.allclose(result, expected), f"Valores esperados {expected}, obtidos {result}"


def test_diferencas_finitas_bvp_constant_source_exact_node():
    """
    Teste end-to-end com f(x)=1, condições de contorno zero, avaliando em nó exato.
    u(x) = (x - x^2)/2
    """
    f = lambda x: np.ones_like(x)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    N = 10
    h = (b - a) / (N + 1)
    k = 5
    x_alvo = a + k * h
    expected = x_alvo * (1 - x_alvo) / 2.0
    result = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
    assert isinstance(result, float), "Resultado deve ser float"
    assert result == pytest.approx(expected, rel=1e-6), (
        f"Para x_alvo={x_alvo}, esperava {expected}, obteve {result}"
    )


def test_diferencas_finitas_bvp_constant_source_at_node_no_interpolation():
    """
    Teste end-to-end com f(x)=1 e bc zero, avaliando em nó interno para evitar interpolação.
    u(x) = (x - x^2)/2
    """
    f = lambda x: np.ones_like(x)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    N = 4
    h = (b - a) / (N + 1)
    k = 2  # escolhe o nó interno de índice 2
    x_alvo = a + k * h
    expected = x_alvo * (1 - x_alvo) / 2.0
    result = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
    assert result == pytest.approx(expected, rel=1e-6), (
        f"Para x_alvo nó interno={x_alvo}, esperava {expected}, obteve {result}"
    )


def test_diferencas_finitas_bvp_nonzero_bc_at_node():
    """
    Teste end-to-end com f(x)=1, bc u(0)=0 e u(1)=1, avaliando em nó interno.
    Solução analítica: u(x) = (x - x^2)/2 + x
    """
    f = lambda x: np.ones_like(x)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 1.0}
    N = 10
    h = (b - a) / (N + 1)
    k = 3  # nó interno de índice 3
    x_alvo = a + k * h
    expected = (x_alvo - x_alvo**2) / 2.0 + x_alvo
    result = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
    assert result == pytest.approx(expected, rel=1e-6), (
        f"Para x_alvo nó interno={x_alvo}, esperava {expected}, obteve {result}"
    )
