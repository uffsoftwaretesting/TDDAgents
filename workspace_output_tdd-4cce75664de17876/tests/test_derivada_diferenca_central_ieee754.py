import math
import pytest
from mathutils.derivada_diferenca_central import derivada_diferenca_central


def test_overflow_returns_inf():
    """Test that when f(x+h) yields inf and f(x-h) finite, derivative returns inf."""
    x0 = 710.0
    h = 1.0
    def f(x):
        # Only at x0+h we return inf, otherwise finite
        if math.isclose(x, x0 + h, rel_tol=0, abs_tol=1e-12):
            return float('inf')
        return 0.0
    result = derivada_diferenca_central(f, x0, h)
    # Expect positive infinity per IEEE-754
    assert math.isinf(result)
    assert result > 0


def test_inf_minus_inf_results_in_nan():
    """Test that when f returns inf for both x+h and x-h, derivative returns nan."""
    x0 = 0.0
    h = 1.0
    def f(x):
        # Always returns infinite, so inf - inf => nan
        return float('inf')
    result = derivada_diferenca_central(f, x0, h)
    assert math.isnan(result)
