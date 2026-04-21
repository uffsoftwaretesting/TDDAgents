import pytest
import numpy as np
from diferencas_finitas_bvp.interpolation import _evaluate_at_target


def test_evaluate_at_target_below_lower_bound_returns_first_value():
    """
    Quando x_alvo é menor que o primeiro nó, deve retornar u[0].
    """
    x = np.array([1.0, 2.0, 3.0])
    u = np.array([10.0, 20.0, 30.0])
    x_alvo = 0.5  # abaixo de x[0]
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(10.0), f"Esperava 10.0 para x_alvo={x_alvo}, obteve {result}"


def test_evaluate_at_target_above_upper_bound_returns_last_value():
    """
    Quando x_alvo é maior que o último nó, deve retornar u[-1].
    """
    x = np.array([1.0, 2.0, 3.0])
    u = np.array([10.0, 20.0, 30.0])
    x_alvo = 4.5  # acima de x[-1]
    result = _evaluate_at_target(u, x, x_alvo)
    assert result == pytest.approx(30.0), f"Esperava 30.0 para x_alvo={x_alvo}, obteve {result}"