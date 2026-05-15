import pytest
import math
from src.adams import adams_bashforth_3


def test_ab3_exp_exact_multiple_of_h():
    """
    For f(t,y)=y from t0=0 to t_final=1.0 with h=0.25 (Δ=4·h),
    the AB3 approximation should match exp(1) within tolerance.
    """
    f = lambda t, y: y
    t0, y0 = 0.0, 1.0
    h = 0.25
    t_final = 1.0  # Δ = 1.0 = 4*h

    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = math.exp(1.0)
    assert result == pytest.approx(expected, rel=5e-2)


def test_ab3_exp_with_residual_last_step():
    """
    For f(t,y)=y from t0=0 to t_final=1.0 with h=0.3 (Δ=1.0, floor(Δ/h)=3, residual=0.1),
    the AB3 approximation should match exp(1) within tolerance,
    validating the last adjusted step dt != h.
    """
    f = lambda t, y: y
    t0, y0 = 0.0, 1.0
    h = 0.3
    t_final = 1.0  # Δ = 1.0, 3*h=0.9, last dt=0.1

    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = math.exp(1.0)
    assert result == pytest.approx(expected, rel=5e-2)


def test_ab3_constant_rate_solution():
    """
    For f(t,y)=a*y (a=2) from t0=1.0, y0=2.0 to t_final=1.5 with h=0.2,
    the AB3 approximation should match y0*exp(a*(t_final - t0)).
    """
    a = 2.0
    f = lambda t, y: a * y
    t0, y0 = 1.0, 2.0
    t_final = 1.5  # Δ = 0.5
    h = 0.2        # floor(Δ/h)=2, residual=0.1

    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 * math.exp(a * (t_final - t0))
    assert result == pytest.approx(expected, rel=5e-2)
