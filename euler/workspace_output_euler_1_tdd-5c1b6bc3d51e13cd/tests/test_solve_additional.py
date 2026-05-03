import pytest
import math
from src.solve import solve


def test_propagates_custom_exception():
    """
    Deve propagar exceção customizada lançada por f sem capturar.
    """
    class MyError(Exception):
        pass

    def f(t, y):
        raise MyError("custom")

    with pytest.raises(MyError):
        solve(f, 0.0, 1.0, 0.0, n=1)


def test_infinite_result_propagates_to_output():
    """
    Quando f retorna infinito, o resultado deve ser infinito.
    """
    f = lambda t, y: float('inf')
    result = solve(f, 0.0, 1.0, 0.0, n=1)
    assert math.isinf(result), f"Expected infinite result, got {result}"


def test_euler_quadratic_function_approximation():
    """
    Para f(t, y) = t, o valor analítico em [0,2] com y0=0 é 2. A aproximação deve estar dentro de tolerância.
    """
    f = lambda t, y: t
    t0, tf, y0 = 0.0, 2.0, 0.0
    n = 100
    expected = (tf**2 - t0**2) / 2 + y0
    result = solve(f, t0, tf, y0, n)
    assert result == pytest.approx(expected, rel=1e-2), (
        f"Quadratic approximation with n={n}: expected {expected}, got {result}"
    )


def test_euler_linear_exact_n1():
    """
    Para y' = y, com n=1, o resultado exato de Euler é y0*(1+h).
    """
    f = lambda t, y: y
    t0, y0, tf, n = 0.0, 1.0, 1.0, 1
    h = (tf - t0) / n
    expected = y0 * (1 + h)**n
    result = solve(f, t0, tf, y0, n)
    assert result == pytest.approx(expected), (
        f"Linear exact for n=1: expected {expected}, got {result}"
    )
