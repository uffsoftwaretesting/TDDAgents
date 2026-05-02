import pytest
from src.solver import adams_bashforth_3


def test_multistep_ab3_constant_derivative_uniform_steps():
    """
    Para dy/dt = C e t_final - t0 = N*h (N>=2), a combinação de dois passos RK4 e AB3
    deve ser exata: y_final = y0 + C*(t_final - t0).
    """
    C = 4.5
    def f(t, y):
        return C

    t0 = 0.0
    y0 = 1.0
    h = 0.2
    # Três passos completos: t_final - t0 = 3*h
    t_final = t0 + 3 * h

    expected = y0 + C * (t_final - t0)
    y = adams_bashforth_3(f, t0, y0, t_final, h)
    assert pytest.approx(y, rel=1e-12) == expected


def test_multistep_ab3_constant_derivative_last_small_step():
    """
    Para dy/dt = C e t_final - t0 = N*h + dt_extra (dt_extra < h),
    o último passo deve usar dt_extra e ainda ser exato para derivada constante.
    """
    C = -2.75
    def f(t, y):
        return C

    t0 = 1.0
    y0 = -0.5
    h = 0.3
    # Dois passos completos e um passo final dt_extra = 0.15 (< h)
    dt_extra = 0.15
    t_final = t0 + 2 * h + dt_extra

    expected = y0 + C * (t_final - t0)
    y = adams_bashforth_3(f, t0, y0, t_final, h)
    assert pytest.approx(y, rel=1e-12) == expected
