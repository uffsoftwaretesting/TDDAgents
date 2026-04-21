import pytest
from src.euler_impl.newton_solver import _newton_solver
from src.euler_impl.exceptions import ConvergenceError

def test_newton_solver_converges_linear():
    # φ(y) = y - c, raiz em y = c
    c = 5.0
    phi = lambda y: y - c
    phi_prime = lambda y: 1.0
    y0 = 0.0
    tol = 1e-8
    max_iter = 10
    result = _newton_solver(phi, phi_prime, y0, tol, max_iter)
    assert pytest.approx(c, abs=tol) == result

def test_newton_solver_with_numeric_derivative():
    # φ(y) = y**2, raiz em y = 0, usando derivada numérica central
    phi = lambda y: y**2
    eps = 1e-6
    def phi_prime_numeric(y):
        return (phi(y + eps) - phi(y - eps)) / (2 * eps)
    y0 = 10.0
    tol = 1e-8
    max_iter = 50
    result = _newton_solver(phi, phi_prime_numeric, y0, tol, max_iter)
    assert abs(result) < tol

def test_newton_solver_raises_convergence_error():
    # φ(y) = y - 1, e derivada muito pequena para forçar não-convergência
    phi = lambda y: y - 1.0
    phi_prime = lambda y: 1e-10
    y0 = 0.0
    tol = 1e-8
    max_iter = 1
    with pytest.raises(ConvergenceError):
        _newton_solver(phi, phi_prime, y0, tol, max_iter)
