import pytest
from src.solve import solve


def test_solve_linear_function_negative_integral_b_less_than_a_single_interval():
    # For f(x) = x, integral from 5 to 3 is -8.0 exactly with one interval
    f = lambda x: x
    result = solve(f, 5, 3, 1)
    assert isinstance(result, float)
    assert result == pytest.approx(-8.0)


def test_solve_linear_function_negative_integral_b_less_than_a_two_intervals_equivalence():
    # With two subintervals, composite trapezoid should still give exact result -8.0
    f = lambda x: x
    result_int = solve(f, 5, 3, 2)
    result_float = solve(f, 5.0, 3.0, 2)
    assert isinstance(result_int, float)
    assert isinstance(result_float, float)
    assert result_int == result_float
    assert result_int == pytest.approx(-8.0)


def test_solve_linear_function_negative_integral_b_less_than_a_with_floats():
    # Single interval with floats
    f = lambda x: x
    result = solve(f, 5.0, 3.0, 1)
    assert isinstance(result, float)
    assert result == pytest.approx(-8.0)
