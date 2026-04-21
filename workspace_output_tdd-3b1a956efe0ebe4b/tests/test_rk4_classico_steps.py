import pytest
from src.rk4_classico import rk4_classico


def test_exact_division_steps_and_result():
    # Given a constant derivative f(t, y) = 1, y should increase linearly
    calls = {'count': 0}
    def f(t, y):
        calls['count'] += 1
        return 1.0

    t0 = 0.0
    y0 = 0.0
    t_final = 1.0  # Exactly 10 steps of size 0.1
    h = 0.1

    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect 10 full steps, each with 4 function evaluations
    assert calls['count'] == 4 * 10
    # For constant derivative, RK4 is exact: y = y0 + (t_final - t0)
    assert result == pytest.approx(1.0)


def test_non_exact_division_steps_and_result():
    # Given a constant derivative f(t, y) = 1, y should increase linearly
    calls = {'count': 0}
    def f(t, y):
        calls['count'] += 1
        return 1.0

    t0 = 0.0
    y0 = 0.0
    t_final = 1.05  # 10 full steps of 0.1 plus one final step of 0.05
    h = 0.1

    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect 10 full steps plus 1 last step, each with 4 evaluations
    assert calls['count'] == 4 * 11
    # For constant derivative, RK4 is exact: y = y0 + (t_final - t0)
    assert result == pytest.approx(1.05)
