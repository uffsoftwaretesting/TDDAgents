import pytest
import math
from src.integracao import integracao_simpson_1_3


def test_quadratic_function_exact_with_n10():
    # ∫[0,2] x^2 dx = (2^3 - 0^3)/3 = 8/3 exato
    def f(x):
        return x**2
    a, b = 0.0, 2.0
    N = 10
    result = integracao_simpson_1_3(f, a, b, N)
    expected = (b**3 - a**3) / 3
    assert result == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_cosine_function_convergence_n10():
    # ∫[0,π/2] cos(x) dx = 1.0
    f = math.cos
    a, b = 0.0, math.pi / 2
    N = 10
    result = integracao_simpson_1_3(f, a, b, N)
    expected = math.sin(b) - math.sin(a)
    assert result == pytest.approx(expected, rel=1e-5)
