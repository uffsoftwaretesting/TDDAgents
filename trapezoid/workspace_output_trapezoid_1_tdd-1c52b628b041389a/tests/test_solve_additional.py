import pytest
from src.solve import solve

def test_constant_function_integration_on_positive_interval():
    # f(x) = 3.5 on [0,4], exact integral = 3.5 * 4 = 14.0
    f = lambda x: 3.5
    result = solve(f, 0, 4, 10)
    assert isinstance(result, float)
    assert result == pytest.approx(14.0)


def test_constant_function_integration_single_interval():
    # f(x) = -2.0 on [1,5], exact integral = -2.0 * (5-1) = -8.0
    f = lambda x: -2.0
    result = solve(f, 1, 5, 1)
    assert isinstance(result, float)
    assert result == pytest.approx(-8.0)


def test_symmetric_interval_linear_zero_result():
    # f(x) = x on [-1,1], exact integral = 0.0
    f = lambda x: x
    result = solve(f, -1, 1, 50)
    assert isinstance(result, float)
    assert result == pytest.approx(0.0)


def test_symmetric_interval_quadratic():
    # f(x) = x^2 on [-1,1], exact integral = 2/3 ~ 0.6666667
    f = lambda x: x**2
    exact = 2.0/3.0
    # use an odd number of subintervals to cover all midpoints symmetrically
    result = solve(f, -1, 1, 101)
    assert isinstance(result, float)
    # allow trapezoidal O(h^2) error: use rel=1e-3
    assert result == pytest.approx(exact, rel=1e-3)
