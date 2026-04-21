import pytest
from src.solver_euler import euler_explicito

@ pytest.mark.parametrize("h", [0.0, -0.1])
def test_h_not_positive_raises_value_error(h):
    """
    h <= 0 should raise ValueError("h must be positive").
    """
    with pytest.raises(ValueError, match="h must be positive"):
        euler_explicito(lambda t, y: t + y, 0.0, 1.0, 1.0, h)


def test_t_final_less_than_t0_raises_value_error():
    """
    t_final < t0 should raise ValueError("t_final must be >= t0").
    """
    with pytest.raises(ValueError, match="t_final must be >= t0"):
        euler_explicito(lambda t, y: t + y, 2.0, 1.0, 1.0, 0.1)

@ pytest.mark.parametrize("t0, t_final, h", [
    (0.0, 1.0, 0.3),
    (0.0, 1.0, 0.3333),
    (1.0, 2.0, 0.3333),
])
def test_number_of_steps_not_integer_raises_value_error(t0, t_final, h):
    """
    When N = (t_final - t0)/h is not integer within tol, should raise ValueError("Number of steps must be integer").
    """
    with pytest.raises(ValueError, match="Number of steps must be integer"):
        euler_explicito(lambda t, y: t + y, t0, 1.0, t_final, h)
