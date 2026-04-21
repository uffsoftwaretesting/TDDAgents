import math
import pytest
from interpolacao_lagrange import interpolacao_lagrange


def test_quadratic_interpolation_three_points():
    # f(x) = x ** 2; nós em [0.0, 1.0, 2.0]; x_alvo = 1.5 => esperado 2.25
    x_nos = [0.0, 1.0, 2.0]

    def f(x):
        return x ** 2

    result = interpolacao_lagrange(x_nos, f, 1.5)
    assert pytest.approx(result, rel=1e-12) == 2.25


def test_quadratic_extrapolation_outside_range():
    # f(x) = x ** 2; nós em [0.0, 1.0, 2.0]; x_alvo = 3.0 => esperado 9.0
    x_nos = [0.0, 1.0, 2.0]

    def f(x):
        return x ** 2

    result = interpolacao_lagrange(x_nos, f, 3.0)
    assert pytest.approx(result, rel=1e-12) == 9.0


def test_interpolation_matches_function_at_nodes():
    # Para qualquer f, interpolação em cada nó deve retornar f(nó)
    x_nos = [0.0, 1.0, 2.0, 3.0]

    def f(x):
        return math.sin(x)

    for xi in x_nos:
        result = interpolacao_lagrange(x_nos, f, xi)
        assert pytest.approx(result, rel=1e-12) == f(xi)


def test_linear_function_interpolation_for_two_points():
    # f(x) = 2 * x + 3; nós em [1.0, 4.0]; x_alvo = 2.5 => esperado 8.0
    x_nos = [1.0, 4.0]

    def f(x):
        return 2 * x + 3

    result = interpolacao_lagrange(x_nos, f, 2.5)
    assert pytest.approx(result, rel=1e-12) == 8.0
