import pytest
from src.solver_euler import euler_explicito

@ pytest.mark.parametrize("delta_N", [1e-8, -1e-8, 1e-9])
def test_steps_within_tolerance_compute_integer_steps(delta_N):
    """
    When N = (t_final - t0)/h is within 1e-8 of an integer,
    the solver should accept it, round to that integer, and run exactly N steps.
    """
    t0 = 0.0
    y0 = 1.23
    h = 0.1
    N_base = 10
    # t_final chosen so that N_float = N_base + delta_N
    t_final = t0 + h * (N_base + delta_N)

    calls = []
    def f(t, y):
        # record each call; derivative returns 0 to keep y constant
        calls.append((t, y))
        return 0.0

    # Should not raise, and return the original y0
    result = euler_explicito(f, t0, y0, t_final, h)
    assert result == pytest.approx(y0), \
        f"Expected result {y0} when dy=0, got {result}"

    # Expect exactly N_base calls
    assert len(calls) == N_base, \
        f"Expected {N_base} steps, but got {len(calls)}"

    # Verify the sequence of t and y passed to f
    for i, (t_val, y_val) in enumerate(calls):
        expected_t = t0 + i * h
        assert t_val == pytest.approx(expected_t), \
            f"At step {i}, expected t={expected_t}, got {t_val}"
        assert y_val == pytest.approx(y0), \
            f"At step {i}, expected y={y0}, got {y_val}"