import math
import pytest
from src.euler_impl.euler_implicit import euler_implicito


def test_last_step_adjustment_constant_rhs():
    """
    If t_final - t0 is not a multiple of h, the last h_i should be adjusted
    so that the final time equals t_final, and the solution matches the
    exact integral for a constant RHS.
    """
    # Parameters for the test
    t0 = 0.0
    y0 = 1.0
    f_const = 3.0
    h = 0.1
    t_final = 0.35

    # Run the solver
    ys = euler_implicito(
        func=lambda t, y: f_const,
        t0=t0,
        y0=y0,
        t_final=t_final,
        h=h
    )

    # Expected number of steps
    n_steps = math.ceil((t_final - t0) / h)
    assert len(ys) == n_steps + 1

    # Reconstruct the time grid, with adjusted last step
    times = [t0]
    current = t0
    for step in range(n_steps):
        if step < n_steps - 1:
            h_i = h
        else:
            h_i = t_final - current
        current += h_i
        times.append(current)

    # Compute expected y values: y(t) = y0 + f_const * t
    expected = [y0 + f_const * t for t in times]

    # Compare each computed y with expected
    for y_res, y_exp in zip(ys, expected):
        assert pytest.approx(y_exp, rel=1e-8) == y_res


def test_no_adjustment_when_multiple_of_h():
    """
    If t_final - t0 is an exact multiple of h, all steps should be equal to h,
    and the solution matches the exact integral for a constant RHS.
    """
    # Parameters for the test
    t0 = 0.0
    y0 = 2.0
    f_const = 5.0
    h = 0.1
    t_final = 0.3  # exactly 3 steps of size 0.1

    # Run the solver
    ys = euler_implicito(
        func=lambda t, y: f_const,
        t0=t0,
        y0=y0,
        t_final=t_final,
        h=h
    )

    # Expected number of steps
    n_steps = math.ceil((t_final - t0) / h)
    assert len(ys) == n_steps + 1

    # Construct uniform time grid
    times = [t0 + i * h for i in range(n_steps + 1)]

    # Compute expected y values: y(t) = y0 + f_const * t
    expected = [y0 + f_const * t for t in times]

    # Compare each computed y with expected
    for y_res, y_exp in zip(ys, expected):
        assert pytest.approx(y_exp, rel=1e-8) == y_res
