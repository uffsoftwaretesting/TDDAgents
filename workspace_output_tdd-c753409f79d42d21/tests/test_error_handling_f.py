import pytest
from src.solver_euler import euler_explicito

@ pytest.mark.parametrize("error_step", [0, 1, 2])
def test_error_in_derivative_is_wrapped_as_runtime_error(error_step):
    """
    If the derivative function f raises an exception at step i,
    euler_explicito should catch it and rethrow as a RuntimeError
    with the message: "Error evaluating derivative at step i: {e}".
    """
    calls = {"count": 0}

    def f(t, y):
        i = calls["count"]
        calls["count"] += 1
        if i == error_step:
            # simulate an internal error in f
            raise ValueError("oops")
        return 0.0  # normal derivative value

    t0 = 0.0
    y0 = 1.0
    h = 1.0
    # Choose t_final to yield N=3 steps
    t_final = 3.0

    expected_message = f"Error evaluating derivative at step {error_step}: oops"
    with pytest.raises(RuntimeError) as excinfo:
        euler_explicito(f, t0, y0, t_final, h)
    # Ensure the error message matches exactly
    assert expected_message in str(excinfo.value), (
        f"Expected RuntimeError with message '{expected_message}', got '{excinfo.value}'"
    )
