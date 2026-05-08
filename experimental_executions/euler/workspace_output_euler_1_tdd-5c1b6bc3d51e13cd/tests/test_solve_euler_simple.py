import pytest
from src.solve import solve

def test_euler_constant_function_basic():
    """
    Para f(t, y) constante, solve deve retornar y0 + c*(tf-t0) exato para n=1.
    """
    c = 3.0
    f = lambda t, y: c
    t0 = 0.0
    y0 = 1.0
    tf = 2.0
    n = 1
    expected = y0 + c * (tf - t0)
    result = solve(f, t0, tf, y0, n)
    assert isinstance(result, float), "O retorno deve ser float"
    assert result == pytest.approx(expected), f"Esperado {expected}, obtido {result}"

@pytest.mark.parametrize("n", [1, 2])
def test_euler_constant_function_various_steps(n):
    """
    Para f(t, y) constante, solve deve retornar y0 + c*(tf-t0) exato para n=1 e n=2.
    """
    c = 5.0
    f = lambda t, y: c
    t0 = 1.0
    y0 = -2.0
    tf = 4.0
    expected = y0 + c * (tf - t0)
    result = solve(f, t0, tf, y0, n)
    assert result == pytest.approx(expected), (
        f"Para n={n}, esperado {expected}, obtido {result}"
    )
