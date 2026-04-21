import math
import pytest
from src.rk2_heun import rk2_heun


def test_integration_constant_derivative_h_larger_than_interval():
    """
    When h > (t_final - t0), there should be exactly one remainder step
    yielding the correct result for a constant derivative.
    """
    t0 = 0.0
    y0 = 0.0
    h = 2.0
    t_final = 1.0
    k = 5.0
    calls = []

    def f(t, y):
        calls.append((t, y))
        return k

    y_res = rk2_heun(f, t0, y0, t_final, h)
    expected = y0 + k * (t_final - t0)
    assert y_res == pytest.approx(expected)
    # One step => two calls
    assert len(calls) == 2


def test_exponential_growth_accuracy_relative_tolerance():
    """
    Solve y' = y over [0, 2] with h=0.25 and compare to exp(2)
    using a relative tolerance for numeric accuracy.
    """
    t0 = 0.0
    y0 = 1.0
    h = 0.25
    t_final = 2.0

    def f(t, y):
        return y

    y_res = rk2_heun(f, t0, y0, t_final, h)
    expected = math.exp(t_final - t0)
    # Allow relative error up to 2% for RK2 method
    assert y_res == pytest.approx(expected, rel=2e-2)


def test_sine_integral_accuracy_relative_tolerance():
    """
    Integrate y' = sin(t) from 0 to pi, expecting y = 1 - cos(pi) = 2,
    with relative tolerance to validate precision.
    """
    t0 = 0.0
    y0 = 0.0
    h = 0.1
    t_final = math.pi

    def f(t, y):
        return math.sin(t)

    y_res = rk2_heun(f, t0, y0, t_final, h)
    expected = 1.0 - math.cos(t_final)
    assert y_res == pytest.approx(expected, rel=1e-3)


def test_extremely_small_remainder_integration_accuracy():
    """
    Use a floating-point literal that induces a tiny remainder step,
    ensure the final result matches the expected integral of y'=1,
    and verify that an extra step was performed.
    """
    h = 0.1
    t0 = 0.0
    # literal forces a tiny rounding error remainder > 0
    t_final = 0.30000000000000004
    y0 = 0.0
    calls = []

    def f(t, y):
        calls.append((t, y))
        return 1.0

    y_res = rk2_heun(f, t0, y0, t_final, h)
    expected = t_final  # integral of 1 over the interval
    # Tight relative tolerance for the small interval
    assert y_res == pytest.approx(expected, rel=1e-9)

    # Verify an extra remainder step was taken
    interval = t_final - t0
    n_full = int(math.floor(interval / h))
    # remainder > 0 => one extra step
    expected_steps = n_full + 1
    assert len(calls) == 2 * expected_steps
