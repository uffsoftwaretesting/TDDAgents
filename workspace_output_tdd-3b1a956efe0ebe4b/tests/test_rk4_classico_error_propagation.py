import pytest
from src.rk4_classico import rk4_classico

def test_f_raises_custom_exception_propagates():
    # If f raises an exception, rk4_classico should propagate it
    def f(t, y):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        # One step only (h==t_final), exception occurs at k1
        rk4_classico(f, 0.0, 1.0, 0.1, 0.1)


def test_f_returns_non_numeric_raises_type_error():
    # If f returns a non-numeric type, arithmetic should fail with TypeError
    def f(t, y):
        return "not a float"

    with pytest.raises(TypeError):
        # A single partial step to trigger arithmetic
        rk4_classico(f, 0.0, 1.0, 0.05, 0.1)


def test_f_returns_int_works_correctly():
    # If f returns ints, arithmetic mixes float and int to produce floats
    calls = {'count': 0}
    def f(t, y):
        calls['count'] += 1
        return 1  # int value

    t0 = 0.0
    y0 = 0.0
    t_final = 1.0
    h = 0.25

    result = rk4_classico(f, t0, y0, t_final, h)

    # Expect 4 full steps (1.0/0.25) each with 4 evaluations
    assert calls['count'] == 4 * 4
    # For constant derivative of 1, y should increase by exactly 1.0
    assert result == pytest.approx(1.0)
