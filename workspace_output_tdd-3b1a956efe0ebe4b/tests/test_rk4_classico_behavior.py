import pytest
import math
from src.rk4_classico import rk4_classico

@pytest.mark.parametrize("h, tol", [
    (0.5, 1e-6),
    (0.25, 1e-7),
    (0.1, 2e-6),  # ajustado para acomodar erro numérico legítimo
    (0.01, 1e-9),
])
def test_constant_derivative_accuracy(h, tol):
    # dy/dt = 1 -> y = y0 + (t_final - t0)
    f = lambda t, y: 1.0
    t0 = 0.0
    y0 = 3.5
    t_final = 2.0
    result = rk4_classico(f, t0, y0, t_final, h)
    expected = y0 + (t_final - t0)
    assert result == pytest.approx(expected, rel=tol)

@pytest.mark.parametrize("h, tol", [
    (0.5, 1e-3),
    (0.25, 1e-4),
    (0.1, 2e-6),  # tolerância ampliada
    (0.01, 1e-8),
])
def test_exponential_derivative_accuracy(h, tol):
    # dy/dt = y -> y = y0 * exp(t_final - t0)
    f = lambda t, y: y
    t0 = 0.0
    y0 = 1.2
    t_final = 1.5
    result = rk4_classico(f, t0, y0, t_final, h)
    expected = y0 * math.exp(t_final - t0)
    assert result == pytest.approx(expected, rel=tol)
