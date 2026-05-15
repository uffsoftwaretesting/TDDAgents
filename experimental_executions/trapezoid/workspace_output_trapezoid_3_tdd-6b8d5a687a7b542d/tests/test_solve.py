import pytest
import math
from src.solve import solve

def test_linear_single_trapezoid():
    # ∫₀¹ x dx = 0.5 exato com n=1
    assert solve(lambda x: x, 0, 1, 1) == pytest.approx(0.5)

def test_polynomial_convergence():
    # ∫₀¹ x² dx = 1/3; convergência com n grande
    result = solve(lambda x: x**2, 0, 1, 1000)
    assert result == pytest.approx(1/3, rel=1e-6)

def test_sine_integral():
    # ∫₀^π sin(x) dx = 2; precisão com n grande
    result = solve(math.sin, 0, math.pi, 1000)
    assert result == pytest.approx(2.0, rel=1e-6)

def test_interval_inverted():
    # ∫₁⁰ x dx = -0.5 para f(x)=x com n=1
    assert solve(lambda x: x, 1, 0, 1) == pytest.approx(-0.5)

def test_zero_length_interval():
    # a == b deve resultar em 0.0
    assert solve(lambda x: x + 1, 2, 2, 10) == pytest.approx(0.0)

def test_non_callable_f():
    # f não callable ➞ TypeError
    with pytest.raises(TypeError):
        solve(5, 0, 1, 10)

def test_invalid_n():
    # n <= 0 ➞ ValueError
    with pytest.raises(ValueError):
        solve(lambda x: x, 0, 1, 0)

def test_invalid_bounds():
    # b não numérico ➞ TypeError
    with pytest.raises(TypeError):
        solve(lambda x: x, 0, 'b', 10)
