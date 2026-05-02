import pytest
from src.solve import solve


def test_constant_function_exact_for_any_n():
    # ∫_{-3}^{5} 7 dx = 7 * (5 - (-3)) = 56
    for n in [1, 2, 5, 10, 100]:
        result = solve(lambda x: 7, -3, 5, n)
        assert result == pytest.approx(56.0)


def test_linear_function_exact_for_any_n():
    # f(x) = 3x + 2
    # ∫_{0}^{2} (3x+2) dx = [3/2 x^2 + 2x]_{0}^{2} = 3/2*4 + 4 = 6 + 4 = 10
    linear = lambda x: 3*x + 2
    expected = 10.0
    for n in [1, 2, 3, 5, 10, 100]:
        result = solve(linear, 0, 2, n)
        assert result == pytest.approx(expected)


def test_trapezoid_weights_matching_manual_calculation():
    # Testa manualmente para n=2 em um polinômio linear:
    # a=1, b=4, f(x)=2x
    # h = (4 - 1) / 2 = 1.5
    # pontos: x0=1, x1=2.5, x2=4
    # total = f(1) + 2*f(2.5) + f(4) = 2*1 + 2*(2*2.5) + 2*4 = 2 + 2*5 + 8 = 2 + 10 + 8 = 20
    # resultado = (h/2) * total = (1.5/2) * 20 = 0.75 * 20 = 15
    f = lambda x: 2*x
    result = solve(f, 1, 4, 2)
    assert result == pytest.approx(15.0)
