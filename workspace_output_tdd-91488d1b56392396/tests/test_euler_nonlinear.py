import pytest
from src.euler_impl.euler_implicit import euler_implicito


def analytic_y_squared(t, y0):
    """
    Analytic solution for dy/dt = y^2 with y(0)=y0: y(t) = y0 / (1 - y0 * t)
    """
    return y0 / (1 - y0 * t)


def test_nonlinear_reference_solution():
    """
    For dy/dt = y^2, compare the implicit solver against the analytic solution at t_final.
    """
    y0 = 1.0
    t0 = 0.0
    t_final = 0.5
    h = 0.01
    # Solve numerically
    ys = euler_implicito(func=lambda t, y: y**2,
                          t0=t0,
                          y0=y0,
                          t_final=t_final,
                          h=h)
    y_num = ys[-1]
    # Analytic solution at t_final
    y_true = analytic_y_squared(t_final, y0)
    # Allow larger relative tolerance for implicit Euler error
    assert pytest.approx(y_true, rel=5e-2) == y_num


def test_nonlinear_convergence_rate():
    """
    Verify that the numerical error decreases when halving the step size.
    """
    y0 = 1.0
    t0 = 0.0
    t_final = 0.5
    # coarse and fine step sizes
    h_coarse = 0.1
    h_fine = 0.05
    ys_coarse = euler_implicito(func=lambda t, y: y**2,
                                 t0=t0,
                                 y0=y0,
                                 t_final=t_final,
                                 h=h_coarse)
    ys_fine = euler_implicito(func=lambda t, y: y**2,
                               t0=t0,
                               y0=y0,
                               t_final=t_final,
                               h=h_fine)
    err_coarse = abs(ys_coarse[-1] - analytic_y_squared(t_final, y0))
    err_fine = abs(ys_fine[-1] - analytic_y_squared(t_final, y0))
    # Fine grid error should be smaller than coarse grid error
    assert err_fine < err_coarse


def test_nonlinear_monotonic_increase():
    """
    The solution for dy/dt = y^2 with positive y0 should be strictly increasing.
    """
    y0 = 0.5
    t0 = 0.0
    t_final = 0.4
    h = 0.05
    ys = euler_implicito(func=lambda t, y: y**2,
                          t0=t0,
                          y0=y0,
                          t_final=t_final,
                          h=h)
    # Each step value should be larger than the previous
    assert all(ys[i+1] > ys[i] for i in range(len(ys)-1))


def test_nonlinear_monotonic_decrease_negative():
    """
    For dy/dt = -y^2 with positive y0, the solution should be strictly decreasing.
    """
    y0 = 1.0
    t0 = 0.0
    t_final = 1.0
    h = 0.1
    ys = euler_implicito(func=lambda t, y: -y**2,
                          t0=t0,
                          y0=y0,
                          t_final=t_final,
                          h=h)
    # Each step value should be smaller than the previous
    assert all(ys[i+1] < ys[i] for i in range(len(ys)-1))