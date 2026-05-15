import pytest
import math
from src.solve import solve


def test_trapezio_linear_n2():
    # f(x)=x over [0,2] with n=2 should integrate exactly to 2.0
    f = lambda x: x
    result = solve(f, 0, 2, 2)
    assert pytest.approx(result, rel=1e-12) == 2.0


def test_trapezio_linear_n3():
    # f(x)=x over [1,3] with n=3 should integrate exactly to 4.0
    f = lambda x: x
    result = solve(f, 1, 3, 3)
    assert pytest.approx(result, rel=1e-12) == 4.0


def test_trapezio_constant_n5():
    # f(x)=7 over [0,10] with n=5 should integrate exactly to 70.0
    f = lambda x: 7.0
    result = solve(f, 0, 10, 5)
    assert pytest.approx(result, rel=1e-12) == 70.0

@ pytest.mark.parametrize("n, expected", [
    (2, 0.375),  # for f(x)=x^2 over [0,1], n=2 -> 3/8
    (4, 0.34375),  # for n=4 -> 11/32
])
def test_trapezio_quadratic_small_n(n, expected):
    # f(x)=x^2 over [0,1], check known approximations for small n
    f = lambda x: x**2
    result = solve(f, 0, 1, n)
    assert pytest.approx(result, rel=1e-12) == expected
