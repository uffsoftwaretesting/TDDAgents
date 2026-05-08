import pytest
from src.solve import solve


def test_solve_zero_derivative():
    # Para f(t, y) = 0, a solução deve permanecer constante em y0
    result = solve(lambda t, y: 0, 0.0, 1.0, 5.0, 10)
    assert result == 5.0


def test_solve_invalid_f_not_callable():
    # f must be a callable
    with pytest.raises(TypeError) as excinfo:
        solve(42, 0.0, 1.0, 1.0, 10)
    assert str(excinfo.value) == "f must be a callable accepting (float, float) and returning float"


def test_solve_invalid_t0_tf_y0_types():
    # t0, tf and y0 must be floats
    with pytest.raises(TypeError) as excinfo:
        solve(lambda t, y: t + y, "0.0", 1.0, 1.0, 10)
    assert str(excinfo.value) == "t0, tf and y0 must be floats"


def test_solve_invalid_n_not_int():
    # n must be an integer
    with pytest.raises(TypeError) as excinfo:
        solve(lambda t, y: t + y, 0.0, 1.0, 1.0, 10.5)
    assert str(excinfo.value) == "n must be an integer"


def test_solve_invalid_n_non_positive():
    # n must be a positive integer
    with pytest.raises(ValueError) as excinfo:
        solve(lambda t, y: t + y, 0.0, 1.0, 1.0, 0)
    assert str(excinfo.value) == "n must be a positive integer"


def test_solve_invalid_t0_ge_tf():
    # tf must be greater than t0
    with pytest.raises(ValueError) as excinfo:
        solve(lambda t, y: t + y, 1.0, 0.0, 1.0, 10)
    assert str(excinfo.value) == "tf must be greater than t0"

# New tests for comprehensive validation

def test_solve_f_returns_non_float():
    # f returns non-float should raise TypeError
    def f_bad(t, y):
        return "not a float"
    with pytest.raises(TypeError):
        solve(f_bad, 0.0, 1.0, 0.0, 10)


def test_solve_invalid_tf_equal_t0():
    # tf equal to t0 should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda t, y: t + y, 1.0, 1.0, 1.0, 10)
    assert str(excinfo.value) == "tf must be greater than t0"


def test_solve_invalid_n_negative():
    # n negative should raise ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda t, y: t + y, 0.0, 1.0, 1.0, -5)
    assert str(excinfo.value) == "n must be a positive integer"


def test_solve_invalid_tf_type():
    # tf not float should raise TypeError
    with pytest.raises(TypeError) as excinfo:
        solve(lambda t, y: t + y, 0.0, "1.0", 1.0, 10)
    assert str(excinfo.value) == "t0, tf and y0 must be floats"


def test_solve_invalid_y0_type():
    # y0 not float should raise TypeError
    with pytest.raises(TypeError) as excinfo:
        solve(lambda t, y: t + y, 0.0, 1.0, 1, 10)
    assert str(excinfo.value) == "t0, tf and y0 must be floats"


def test_step_size_and_time_sequence():
    # Validate that h = (tf - t0)/n and times passed to f are correct
    times = []
    def f_collect(t, y):
        times.append(t)
        return 0.0

    t0 = 1.0
    tf = 3.0
    n = 4
    y0 = 2.5

    result = solve(f_collect, t0, tf, y0, n)
    # solution should remain y0 since derivative is zero
    assert result == y0

    # expected step size and sequence of times
    expected_h = (tf - t0) / n
    expected_times = [t0 + k * expected_h for k in range(n)]
    assert times == expected_times
