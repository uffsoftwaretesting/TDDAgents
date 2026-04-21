import math
import pytest
from src.euler_impl.euler_implicit import euler_implicito
from src.euler_impl.exceptions import ConvergenceError


def test_integration_linear_decay():
    """
    For dy/dt = -k * y, the implicit Euler update is y[n] = y0 / (1 + k*h)**n.
    Verify discrete values match analytic implicit-Euler formula.
    """
    k = 1.5
    y0 = 2.0
    t0 = 0.0
    h = 0.2
    t_final = 1.0
    n_steps = math.ceil((t_final - t0) / h)

    result = euler_implicito(
        func=lambda t, y: -k * y,
        t0=t0,
        y0=y0,
        t_final=t_final,
        h=h
    )
    # Check length
    assert len(result) == n_steps + 1
    # Compare each step with analytic implicit-Euler solution
    for n, y_res in enumerate(result):
        y_exp = y0 / (1 + k * h) ** n
        assert pytest.approx(y_exp, rel=1e-6) == y_res


def test_integration_convergence_error_raises():
    """
    Use a non-linear RHS y**2 with max_iter=1: Newton solver should not converge
    in the first step and raise ConvergenceError from euler_implicito.
    """
    with pytest.raises(ConvergenceError):
        euler_implicito(
            func=lambda t, y: y ** 2,
            t0=0.0,
            y0=1.0,
            t_final=0.1,
            h=0.1,
            tol=1e-8,
            max_iter=1
        )


def test_integration_convergence_order_linear():
    """
    For the linear decay problem, halving the step size should reduce the integration error
    at t_final when compared to the analytic continuous solution y_true = y0 * exp(-k*t).
    """
    k = 0.5
    y0 = 1.0
    t0 = 0.0
    t_final = 1.0
    # Analytic continuous solution
    y_true = y0 * math.exp(-k * t_final)

    # Coarse and fine steps
    h_coarse = 0.5
    h_fine = 0.25
    ys_coarse = euler_implicito(
        func=lambda t, y: -k * y,
        t0=t0,
        y0=y0,
        t_final=t_final,
        h=h_coarse
    )
    ys_fine = euler_implicito(
        func=lambda t, y: -k * y,
        t0=t0,
        y0=y0,
        t_final=t_final,
        h=h_fine
    )
    err_coarse = abs(ys_coarse[-1] - y_true)
    err_fine = abs(ys_fine[-1] - y_true)
    assert err_fine < err_coarse
