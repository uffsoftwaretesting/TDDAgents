import pytest
import math
import numpy as np
from src.solve import solve


def test_x2_convergence_monotonic():
    # ∫₀¹ x² dx = 1/3; verificar que o erro decresce com n crescente
    exact = 1/3
    prev_error = None
    for n in [10, 100, 1000, 10000]:
        result = solve(lambda x: x**2, 0, 1, n)
        error = abs(result - exact)
        # Para n grandes o erro deve ficar abaixo de 1e-2
        assert error < 1e-2
        if prev_error is not None:
            assert error < prev_error
        prev_error = error


def test_sin_integral_accuracy():
    # ∫₀^π sin(x) dx = 2; testar precisão para n>=100
    exact = 2.0
    for n in [100, 1000]:
        result = solve(math.sin, 0, math.pi, n)
        # precisão relativa de 1e-3
        assert result == pytest.approx(exact, rel=1e-3)


def test_compare_with_numpy_trapz():
    # Comparar solve com numpy.trapz no caso de x² em [0,1]
    a, b, n = 0.0, 1.0, 1000
    xs = np.linspace(a, b, n + 1)
    ys = xs**2
    np_result = np.trapz(ys, xs)
    solve_result = solve(lambda x: x**2, a, b, n)
    assert solve_result == pytest.approx(np_result, rel=1e-6)
