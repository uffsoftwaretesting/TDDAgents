import pytest
import numpy as np

from diferencas_finitas_bvp.interpolation import _evaluate_at_target


def test_evaluate_at_target_at_left_boundary_returns_first_value():
    """
    x_alvo igual ao primeiro nó deve retornar u[0] sem interpolação.
    """
    x = np.array([0.0, 1.0, 2.0])
    u = np.array([5.0, 15.0, 25.0])
    x_alvo = x[0]  # 0.0
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(5.0), f"Esperava 5.0, obteve {result}"


def test_evaluate_at_target_at_right_boundary_returns_last_value():
    """
    x_alvo igual ao último nó deve retornar u[-1] sem interpolação.
    """
    x = np.array([-1.0, 0.0, 1.0, 2.0])
    u = np.array([0.0, 10.0, 20.0, 30.0])
    x_alvo = x[-1]  # 2.0
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(30.0), f"Esperava 30.0, obteve {result}"


def test_evaluate_at_target_within_tol_of_node_returns_exact():
    """
    x_alvo está dentro da tolerância de um nó, deve ser tratado como nó exato.
    """
    tol = 1e-12
    x = np.array([0.0, 0.5, 1.0])
    u = np.array([2.0, 4.0, 6.0])
    # Criar um x_alvo ligeiramente diferente de 0.5, mas dentro de tol
    x_alvo = 0.5 + tol / 2
    # Verificar que a diferença absoluta seja menor que tol
    assert abs(x[1] - x_alvo) < tol
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(4.0), f"Esperava 4.0 dado x_alvo ~0.5, obteve {result}"


def test_evaluate_at_target_generic_linear_interpolation():
    """
    Caso genérico: x_alvo entre dois nós, validar fórmula de interpolação.
    """
    x = np.array([10.0, 20.0, 30.0])
    u = np.array([100.0, 200.0, 300.0])
    x_alvo = 15.0  # entre 10 e 20
    # interpolação esperada: 100 + (200-100)*(15-10)/(20-10) = 100 + 100*5/10 = 150
    expected = 150.0
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(expected), f"Esperava {expected}, obteve {result}"