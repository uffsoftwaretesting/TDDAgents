import pytest
from src.rk2_heun import rk2_heun

def test_single_step_constant_derivative():
    # Parameters for a single step
    t0 = 0.0
    y0 = 2.0
    h = 0.5
    t_final = t0 + h
    c = 3.0
    # Spy to record calls
    calls = []
    def f(t, y):
        calls.append((t, y))
        return c
    # Expected manual computation:
    # k1 = c
    # y_pred = y0 + h * k1 = 2.0 + 0.5*3.0 = 3.5
    # k2 = c
    # y_next = y0 + (h/2)*(k1+k2) = 2.0 + 0.25*(6.0) = 3.5
    expected_y = y0 + h * c
    # Run rk2_heun for a single step
    y_result = rk2_heun(f, t0, y0, t_final, h)
    # Check that f was called exactly twice with correct arguments
    assert len(calls) == 2
    # First call at (t0, y0)
    assert calls[0] == pytest.approx((t0, y0))
    # Second call at (t0+h, y_pred)
    y_pred = y0 + h * c
    assert calls[1] == pytest.approx((t0 + h, y_pred))
    # Final result matches expected
    assert y_result == pytest.approx(expected_y)


def test_single_step_linear_derivative():
    # Parameters for a single RK2 step with f(t,y)=t+y
    t0 = 0.0
    y0 = 1.0
    h = 0.2
    t_final = t0 + h
    calls = []
    def f(t, y):
        calls.append((t, y))
        return t + y
    # Manual computation:
    # k1 = f(0.0, 1.0) = 1.0
    # y_pred = 1.0 + 0.2*1.0 = 1.2
    # k2 = f(0.2, 1.2) = 1.4
    # y_next = 1.0 + 0.1*(1.0 + 1.4) = 1.0 + 0.1*2.4 = 1.24
    expected_k1 = y0
    expected_y_pred = y0 + h * expected_k1
    expected_k2 = (t0 + h) + expected_y_pred
    expected_y = y0 + (h / 2) * (expected_k1 + expected_k2)
    # Execute
    y_result = rk2_heun(f, t0, y0, t_final, h)
    # Assert two calls
    assert len(calls) == 2
    # Validate call arguments
    assert calls[0] == pytest.approx((t0, y0))
    assert calls[1] == pytest.approx((t0 + h, expected_y_pred))
    # Validate result
    assert y_result == pytest.approx(expected_y)
