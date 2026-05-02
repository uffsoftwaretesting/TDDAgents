import math
import pytest
from src.solve import solve

@pytest.mark.parametrize("n, tol", [
    (2, 5e-2),      # coarse partition
    (10, 2e-3),     # moderate partition
    (1000, 1e-6),   # fine partition
])
def test_quadratic_convergence(n, tol):
    """
    Tests convergence of the trapezoidal rule on f(x)=x^2 over [0,1].
    Exact integral is 1/3.
    """
    f = lambda x: x**2
    result = solve(f, 0, 1, n)
    exact = 1.0/3.0
    assert result == pytest.approx(exact, abs=tol)

@pytest.mark.parametrize("n, tol", [
    (3, 2e-1),      # coarse partition for sine
    (50, 1e-3),     # moderate partition
    (1000, 1e-5),   # fine partition
])
def test_sine_convergence(n, tol):
    """
    Tests convergence of the trapezoidal rule on f(x)=sin(x) over [0, pi].
    Exact integral is 2.
    """
    f = math.sin
    result = solve(f, 0, math.pi, n)
    exact = 2.0
    assert result == pytest.approx(exact, abs=tol)
