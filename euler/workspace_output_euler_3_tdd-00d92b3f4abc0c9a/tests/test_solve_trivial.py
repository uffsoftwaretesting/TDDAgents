import pytest
from src.solve import solve

@ pytest.mark.parametrize("t0, y0, n", [
    (0, 0, 1),
    (2.5, 7.3, 5),
    (10, -3.14, 100),
    (1e6, 123, 50),
    (-5, 42, 10),
])
def test_tf_equals_t0_returns_initial_y_as_float(t0, y0, n):
    # tf == t0 should return y0 unchanged, as a float, regardless of n > 0
    result = solve(lambda t, y: 9999, t0, t0, y0, n)
    assert isinstance(result, float), "Result must be float when tf == t0"
    assert result == float(y0), f"Expected {float(y0)}, got {result} for t0=tf={t0} and n={n}"
