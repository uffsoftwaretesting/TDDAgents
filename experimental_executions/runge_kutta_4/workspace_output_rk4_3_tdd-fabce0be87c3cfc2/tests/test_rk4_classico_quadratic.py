import pytest
from rk4_classico import rk4_classico

def test_quadratic_derivative_exact_steps():
    # ODE: y' = 2*t, solution y = t^2, 5 steps of h=0.2 from 0.0 to 1.0
    f = lambda t, y: 2.0 * t
    t0 = 0.0
    y0 = 0.0
    t_final = 1.0
    h = 0.2
    result = rk4_classico(f, t0, y0, t_final, h)
    expected = t_final ** 2
    assert result == pytest.approx(expected, rel=1e-6)

def test_quadratic_derivative_non_exact_steps():
    # ODE: y' = 2*t, solution y = t^2, from t0=0.5 to t_final=2.0 with h=0.3
    # last step will be dt=0.2
    f = lambda t, y: 2.0 * t
    t0 = 0.5
    y0 = t0 ** 2
    t_final = 2.0
    h = 0.3
    result = rk4_classico(f, t0, y0, t_final, h)
    expected = t_final ** 2
    assert result == pytest.approx(expected, rel=1e-6)
