import pytest
from src.solve import solve


def test_euler_constant_function_negative_step():
    """
    Para f(t, y) constante e tf < t0, o método de Euler deve retornar
    y0 + c*(tf - t0) exato para qualquer n.
    """
    c = 2.0
    f = lambda t, y: c
    t0 = 5.0
    tf = 1.0  # passo negativo
    y0 = 10.0
    for n in [1, 2, 5, 10]:
        expected = y0 + c * (tf - t0)
        result = solve(f, t0, tf, y0, n)
        assert result == pytest.approx(expected), (
            f"Constant function with n={n}: expected {expected}, got {result}"
        )


@pytest.mark.parametrize("n", [1, 2, 4, 8])
def test_euler_linear_function_negative_step(n):
    """
    Para o EDO y' = y e tf < t0, o método de Euler explícito deve produzir
    y(tf) ≈ y0 * (1 + h)**n, onde h = (tf - t0) / n.
    """
    f = lambda t, y: y
    t0 = 0.0
    tf = -1.0
    y0 = 1.0
    h = (tf - t0) / n
    expected = y0 * (1 + h)**n
    result = solve(f, t0, tf, y0, n)
    assert result == pytest.approx(expected, rel=1e-6), (
        f"Linear function with n={n}: expected {expected}, got {result}"
    )