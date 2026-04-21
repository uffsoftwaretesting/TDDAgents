import pytest
from src.solver_euler import euler_explicito

def failing_derivative(t, y):
    """
    This derivative should never be called when there are zero steps.
    """
    raise AssertionError("Derivative function should not be called when no steps")

@pytest.mark.parametrize("t0, y0, h", [
    (0.0, 1.23, 0.1),
    (2.5, -3.14, 0.5),
    (10.0, 0.0, 0.2),
])
def test_zero_steps_returns_initial_value(t0, y0, h):
    """
    When t_final equals t0 (zero integration steps), the solver should
    immediately return the initial value y0 without calling the derivative.
    """
    result = euler_explicito(failing_derivative, t0, y0, t0, h)
    assert result == y0, (
        f"Expected y0={y0} when t_final==t0, but got {result}"
    )
