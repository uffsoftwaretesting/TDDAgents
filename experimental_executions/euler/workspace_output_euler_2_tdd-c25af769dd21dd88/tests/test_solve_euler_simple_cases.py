import pytest
from src.solve import solve


def test_solve_constant_derivative():
    # For f(t, y) = c constant, solution is y0 + c*(tf - t0)
    c = 2.0
    t0 = 0.0
    tf = 5.0
    y0 = 1.0
    n = 10
    result = solve(lambda t, y: c, t0, tf, y0, n)
    expected = y0 + c * (tf - t0)
    assert result == pytest.approx(expected)


def test_solve_exponential_euler_n1():
    # For f(t, y) = y and n=1, h=1, y1 = y0*(1 + h)
    t0 = 0.0
    tf = 1.0
    y0 = 1.0
    n = 1
    result = solve(lambda t, y: y, t0, tf, y0, n)
    h = (tf - t0) / n
    expected = y0 * (1 + h) ** n
    assert result == pytest.approx(expected)


def test_solve_exponential_euler_n2():
    # For f(t, y) = y and n=2, h=0.5, y2 = y0*(1 + h)^2 = 2.25
    t0 = 0.0
    tf = 1.0
    y0 = 1.0
    n = 2
    result = solve(lambda t, y: y, t0, tf, y0, n)
    h = (tf - t0) / n
    expected = y0 * (1 + h) ** n
    assert result == pytest.approx(expected)
