import pytest
from src.rk4_classico import rk4_classico

def test_constant_derivative_only_adjusted_step():
    # When t_final - t0 < h, only one partial RK4 step should be executed
    calls = {'count': 0}
    def f(t, y):
        calls['count'] += 1
        return 2.0

    t0 = 0.0
    y0 = 1.0
    t_final = 0.05  # Less than h=0.1, so n_steps=0, h_last=0.05
    h = 0.1

    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect exactly 4 evaluations for the single adjusted step
    assert calls['count'] == 4
    # For constant derivative, y = y0 + f * (t_final - t0)
    assert result == pytest.approx(1.0 + 2.0 * 0.05)

def test_linear_derivative_only_adjusted_step_exactness():
    # For f(t, y) = t, the analytic integral from 0 to h_last is (h_last^2)/2
    calls = {'count': 0}
    def f(t, y):
        calls['count'] += 1
        return t

    t0 = 0.0
    y0 = 0.0
    t_final = 0.05  # Only one partial step
    h = 0.1

    result = rk4_classico(f, t0, y0, t_final, h)

    # One RK4 step with 4 evaluations
    assert calls['count'] == 4
    # Analytical result = ∫0 to 0.05 t dt = 0.05^2 / 2 = 0.00125
    assert result == pytest.approx(0.00125)
