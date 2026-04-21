import pytest
from interpolacao_lagrange import interpolacao_lagrange


def test_linear_extrapolation_two_points_below_range():
    # f(x) = 2 * x + 3; nós em [1.0, 4.0]; x_alvo abaixo de 1.0
    def f(x):
        return 2 * x + 3

    x_nos = [1.0, 4.0]
    x_alvo = 0.0
    result = interpolacao_lagrange(x_nos, f, x_alvo)
    assert pytest.approx(result, rel=1e-12) == f(x_alvo)


def test_linear_extrapolation_two_points_above_range():
    # f(x) = 2 * x + 3; nós em [1.0, 4.0]; x_alvo acima de 4.0
    def f(x):
        return 2 * x + 3

    x_nos = [1.0, 4.0]
    x_alvo = 5.0
    result = interpolacao_lagrange(x_nos, f, x_alvo)
    assert pytest.approx(result, rel=1e-12) == f(x_alvo)


def test_quadratic_extrapolation_three_points_below_range():
    # f(x) = x ** 2; nós em [0.0, 1.0, 2.0]; x_alvo abaixo de 0.0
    def f(x):
        return x ** 2

    x_nos = [0.0, 1.0, 2.0]
    x_alvo = -1.0
    result = interpolacao_lagrange(x_nos, f, x_alvo)
    assert pytest.approx(result, rel=1e-12) == f(x_alvo)
