import pytest
from rk4_classico import rk4_classico

def test_zero_derivative_exact_steps():
    # f=0 and h divides the interval exactly, y should remain constant
    t0 = 0.0
    y0 = 10.0
    t_final = 1.0
    h = 0.25
    result = rk4_classico(lambda t, y: 0.0, t0, y0, t_final, h)
    assert result == y0

def test_zero_derivative_non_exact_steps():
    # f=0 and h does not divide the interval exactly, y should remain constant
    t0 = 0.0
    y0 = 5.5
    t_final = 1.0
    h = 0.3
    result = rk4_classico(lambda t, y: 0.0, t0, y0, t_final, h)
    assert result == y0

def test_zero_derivative_large_step():
    # f=0 and h > t_final - t0, y should remain constant in one step
    t0 = 2.0
    y0 = -7.7
    t_final = 3.0
    h = 5.0
    result = rk4_classico(lambda t, y: 0.0, t0, y0, t_final, h)
    assert result == y0