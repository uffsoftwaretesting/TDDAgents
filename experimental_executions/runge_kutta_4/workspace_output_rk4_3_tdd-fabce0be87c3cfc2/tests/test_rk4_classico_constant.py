import pytest
from rk4_classico import rk4_classico

def test_constant_derivative_single_step():
    # Para f(t,y)=1 e t_final = t0 + h, espera-se y = y0 + h
    f = lambda t, y: 1.0
    t0 = 0.0
    y0 = 5.0
    h = 0.2
    t_final = t0 + h
    result = rk4_classico(f, t0, y0, t_final, h)
    assert result == pytest.approx(y0 + h)


def test_constant_derivative_single_step_with_nonzero_t0():
    # Mesma verificação para t0 diferente de zero
    f = lambda t, y: 1.0
    t0 = 1.5
    y0 = -3.0
    h = 0.7
    t_final = t0 + h
    result = rk4_classico(f, t0, y0, t_final, h)
    assert result == pytest.approx(y0 + h)


def test_constant_derivative_multiple_steps_non_exact():
    # ODE: y' = 1, solução y = y0 + (t_final - t0)
    # h não divide exatamente o intervalo, último dt < h
    f = lambda t, y: 1.0
    t0 = 0.0
    y0 = 2.5
    t_final = 1.0
    h = 0.3
    result = rk4_classico(f, t0, y0, t_final, h)
    expected = y0 + (t_final - t0)
    assert result == pytest.approx(expected)
