import pytest
import math

from src.adams_bashforth_3 import adams_bashforth_3


def test_ab3_three_steps_constant_positive():
    """
    Para f constante positiva, o esquema de 3 passos deve produzir
    y_n = y0 + n * h * k
    """
    f = lambda t, y: 3.0
    t0 = 0.0
    y0 = 1.0
    h = 0.2
    n = 3
    t_final = t0 + n * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + n * h * 3.0
    assert result == pytest.approx(expected)


def test_ab3_four_steps_constant_positive():
    """
    Para 4 passos com f constante positiva, verifica crescimento linear
    """
    f = lambda t, y: 1.5
    t0 = 1.0
    y0 = 2.0
    h = 0.5
    n = 4
    t_final = t0 + n * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + n * h * 1.5
    assert result == pytest.approx(expected)


def test_ab3_five_steps_constant_negative():
    """
    Para 5 passos com f constante negativa, verifica decaimento linear
    """
    f = lambda t, y: -2.0
    t0 = -1.0
    y0 = 10.0
    h = 0.1
    n = 5
    t_final = t0 + n * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + n * h * (-2.0)
    assert result == pytest.approx(expected)


def test_ab3_zero_function_multiple_steps():
    """
    Para f = 0, qualquer número de passos deve retornar sempre y0
    """
    f = lambda t, y: 0.0
    t0 = 0.5
    y0 = 5.5
    h = 0.3
    n = 6
    t_final = t0 + n * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    assert result == pytest.approx(y0)


def test_ab3_three_steps_exponential_growth():
    """
    Para y' = y, método de ordem 3 deve aproximar exp(n*h) dentro de O(h^3)
    """
    f = lambda t, y: y
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    n = 3
    t_final = t0 + n * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    exact = math.exp(n * h) * y0
    # Verificar consistência de sinal e que o erro está na ordem de O(h^3)
    assert result > y0
    assert result == pytest.approx(exact, rel=1e-2)
