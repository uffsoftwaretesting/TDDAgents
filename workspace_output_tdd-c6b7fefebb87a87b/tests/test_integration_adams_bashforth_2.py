import math
import pytest
from src.adams_bashforth_2 import adams_bashforth_2


def test_integration_exponential_solution():
    """
    Integração de y' = y, solução analítica y = y0 * exp(t - t0).
    Verifica aproximação com tolerância relativa de 1e-2.
    """
    t0 = 0.0
    y0 = 2.0
    t_eval = 1.0
    h = 0.1
    f = lambda t, y: y

    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    expected = y0 * math.exp(t_eval - t0)
    assert result == pytest.approx(expected, rel=1e-2)


def test_integration_polynomial_solution():
    """
    Integração de y' = t, solução analítica y = y0 + (t_eval^2 - t0^2)/2.
    Verifica aproximação com tolerância absoluta de 5e-2.
    """
    t0 = 1.0
    y0 = 3.0
    t_eval = 2.0
    h = 0.3
    f = lambda t, y: t

    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    expected = y0 + (t_eval**2 - t0**2) / 2
    assert result == pytest.approx(expected, abs=5e-2)
