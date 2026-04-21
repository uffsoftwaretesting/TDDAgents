import pytest
import numpy as np

from diferencas_finitas_bvp.interpolation import _evaluate_at_target


def test_evaluate_at_target_exact_node_returns_value():
    """
    Quando x_alvo coincide com um nó de x, retorna o valor exato de u correspondente.
    """
    x = np.array([0.0, 0.5, 1.0])
    u = np.array([10.0, 20.0, 30.0])
    # Testar para cada nó
    for i, x_val in enumerate(x):
        result = _evaluate_at_target(u, x, x_val)
        assert result == pytest.approx(u[i]), (
            f"Para x_alvo={x_val}, esperava {u[i]}, obteve {result}"
        )


def test_evaluate_at_target_linear_interpolation():
    """
    Quando x_alvo está entre dois nós, aplica interpolação linear corretamente.
    """
    # Casos de teste com intervalos regulares
    # Caso 1: malha simples de 2 pontos
    x1 = np.array([0.0, 1.0])
    u1 = np.array([0.0, 2.0])
    x_alvo1 = 0.25
    expected1 = 0.0 + (2.0 - 0.0) * (x_alvo1 - 0.0) / (1.0 - 0.0)
    result1 = _evaluate_at_target(u1, x1, x_alvo1)
    assert result1 == pytest.approx(expected1), (
        f"Interpolação simples: esperava {expected1}, obteve {result1}"
    )

    # Caso 2: malha com 3 pontos
    x2 = np.array([0.0, 2.0, 4.0])
    u2 = np.array([0.0, 2.0, 4.0])
    x_alvo2 = 3.0  # entre 2.0 e 4.0
    expected2 = 2.0 + (4.0 - 2.0) * (x_alvo2 - 2.0) / (4.0 - 2.0)
    result2 = _evaluate_at_target(u2, x2, x_alvo2)
    assert result2 == pytest.approx(expected2), (
        f"Interpolação em intervalo maior: esperava {expected2}, obteve {result2}"
    )
