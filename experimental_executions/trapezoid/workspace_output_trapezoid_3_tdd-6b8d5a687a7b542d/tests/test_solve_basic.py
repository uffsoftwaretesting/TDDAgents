import pytest
from src.solve import solve

def test_linear_single_trapezoid_basic():
    # Integração básica: ∫₀¹ x dx = 0.5 com n=1
    result = solve(lambda x: x, 0, 1, 1)
    assert result == pytest.approx(0.5)