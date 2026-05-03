import pytest
from src.solve import solve


def test_call_order_standard_interval():
    # For f(x)=x, a=0, b=1, n=4, h=0.25
    calls = []
    def f(x):
        # record each call and return the value for the trapezoid sum
        calls.append(x)
        return x

    result = solve(f, 0, 1, 4)
    # Validate the expected call order: a, b, then interior points
    expected_calls = [0.0, 1.0, 0.25, 0.5, 0.75]
    assert calls == expected_calls, f"Expected call order {expected_calls}, got {calls}"
    # Verify that the result matches the exact integral of x on [0,1]
    assert result == pytest.approx(0.5)


def test_call_order_single_interval():
    # For n=1, only f(a) and f(b) should be called
    calls = []
    def f(x):
        calls.append(x)
        return x * 2  # arbitrary scaling

    result = solve(f, 2, 5, 1)
    expected_calls = [2.0, 5.0]
    assert calls == expected_calls, f"Expected call order {expected_calls}, got {calls}"
    # Integral of 2*x from 2 to 5 is [x^2]_2^5 = 25 - 4 = 21
    assert result == pytest.approx(21.0)


def test_call_order_reversed_interval():
    # For b < a, ensure the same deterministic order with negative step
    calls = []
    def f(x):
        calls.append(x)
        return 1.0  # constant to isolate order

    # a=5, b=3, n=4 -> h = -0.5
    result = solve(f, 5, 3, 4)
    expected_calls = [5.0, 3.0, 4.5, 4.0, 3.5]
    assert calls == expected_calls, f"Expected call order {expected_calls}, got {calls}"
    # Integral of 1 from 5 to 3 is -2.0
    assert result == pytest.approx(-2.0)
