import pytest
import math
from src.solve import solve

def test_solve_propagates_zero_division_error():
    """
    Quando f lança ZeroDivisionError, solve não deve capturar e deve propagar.
    """
    f = lambda t, y: 1/0
    with pytest.raises(ZeroDivisionError):
        solve(f, 0.0, 1.0, 0.0, n=1)


def test_solve_propagates_overflow_error_from_f():
    """
    Quando f lança OverflowError (ex: math.exp(1000)), solve deve propagar.
    """
    def f(t, y):
        return math.exp(1000)
    with pytest.raises(OverflowError):
        solve(f, 0.0, 1.0, 0.0, n=1)
