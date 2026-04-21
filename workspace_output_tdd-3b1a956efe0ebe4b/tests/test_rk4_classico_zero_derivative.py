import pytest
from src.rk4_classico import rk4_classico

def test_zero_derivative_exact_division_keeps_initial_value_and_counts_calls():
    # f(t, y) = 0 should keep y constant and still invoke 4 calls per full step
    calls = {"count": 0}
    def f(t, y):
        calls["count"] += 1
        return 0.0

    t0 = 0.0
    y0 = 5.0
    t_final = 1.0  # exactly 10 steps of size 0.1
    h = 0.1

    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect 10 full steps, each with 4 function evaluations
    assert calls["count"] == 4 * 10
    # For zero derivative, RK4 should leave y unchanged
    assert result == pytest.approx(y0)


def test_zero_derivative_non_exact_division_keeps_initial_value_and_counts_calls():
    # f(t, y) = 0 should keep y constant over full and last partial step
    calls = {"count": 0}
    def f(t, y):
        calls["count"] += 1
        return 0.0

    t0 = 2.0
    y0 = -3.5
    t_final = 2.05  # 10 full steps of 0.01 plus one last step of 0.0? Actually 0.05 remainder
    h = 0.005

    # Compute expected steps
    total_interval = t_final - t0  # 0.05
    n_steps = int(total_interval // h)  # 10 full steps
    h_last = total_interval - n_steps * h  # 0.05 - 10*0.005 = 0.0? Actually 0.05-0.05=0.0
    # But to illustrate partial step, choose h=0.004
    # Let's adjust to ensure a non-zero last step
    h = 0.009
    total_interval = t_final - t0  # 0.05
    n_steps = int(total_interval // h)  # floor(5.555...) = 5
    h_last = total_interval - n_steps * h  # 0.05 - 5*0.009 = 0.005

    calls["count"] = 0
    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect n_steps full steps plus one last partial step, each with 4 evaluations
    expected_calls = 4 * (n_steps + (1 if h_last > 0.0 else 0))
    assert calls["count"] == expected_calls
    # For zero derivative, RK4 should leave y unchanged
    assert result == pytest.approx(y0)