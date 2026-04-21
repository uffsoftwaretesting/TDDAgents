import math
import pytest
import src.euler_impl.euler_implicit as euler_mod
from src.euler_impl.euler_implicit import euler_implicito


def test_linear_ode_euler_implicit_basic_solution():
    """
    For dy/dt = a*y, verify that Euler Implicit computes
    y[n] = c / (1 - a*h)^n and returns the correct number of steps.
    """
    a = 2.0
    c = 1.5  # initial value y0
    t0 = 0.0
    h = 0.1
    t_final = 0.5  # should yield 5 steps exactly
    n_steps = math.ceil((t_final - t0) / h)
    # Expected discrete solution y_n = c / (1 - a*h)^n
    expected = [c / (1 - a * h) ** n for n in range(n_steps + 1)]

    result = euler_implicito(func=lambda t, y: a * y,
                              t0=t0,
                              y0=c,
                              t_final=t_final,
                              h=h)

    # Check length and each value
    assert len(result) == n_steps + 1
    for y_res, y_exp in zip(result, expected):
        assert pytest.approx(y_exp, rel=1e-6) == y_res


def test_newton_solver_called_each_step(monkeypatch):
    """
    Monkeypatch the internal _newton_solver to count calls.
    For an interval requiring N steps, it should be called N times.
    """
    calls = []

    def fake_newton(phi, phi_prime, y_init, tol, max_iter):
        # Record that _newton_solver was called with these args
        calls.append((phi, phi_prime, y_init, tol, max_iter))
        # Return the initial guess to keep y constant
        return y_init

    # Patch the solver in the module under test
    monkeypatch.setattr(euler_mod, '_newton_solver', fake_newton)

    a = 1.0
    c = 1.0
    t0 = 0.0
    h = 0.2
    t_final = 0.7  # ceil((0.7-0)/0.2) = 4 steps
    n_steps = math.ceil((t_final - t0) / h)

    result = euler_implicito(func=lambda t, y: a * y,
                              t0=t0,
                              y0=c,
                              t_final=t_final,
                              h=h)

    # _newton_solver should be called once per step
    assert len(calls) == n_steps
    # Since fake_newton returns y_init each time, result should be constant list
    assert result == [c] * (n_steps + 1)
