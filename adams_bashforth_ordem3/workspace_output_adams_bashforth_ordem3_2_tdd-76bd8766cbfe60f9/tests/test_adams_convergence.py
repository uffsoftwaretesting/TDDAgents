import pytest
import math
from src.adams import adams_bashforth_3


def test_adams_convergence_order3():
    """
    Verify that adams_bashforth_3 exhibits third-order convergence for y'=y, y(0)=1,
    by checking that error ratios for halved step sizes scale like 2^3=8.
    """
    f = lambda t, y: y
    t0, y0 = 0.0, 1.0
    t_final = 1.0
    # Step sizes halved: expect error ~ C*h^3, so error ratio ~ (h1/h2)^3
    hs = [0.2, 0.1, 0.05]
    errors = []
    for h in hs:
        y_approx = adams_bashforth_3(f, t0, y0, t_final, h)
        errors.append(abs(y_approx - math.exp(1.0)))
    # Check convergence rates between consecutive h
    for i in range(len(hs) - 1):
        h1, h2 = hs[i], hs[i + 1]
        e1, e2 = errors[i], errors[i + 1]
        # Compute observed order: log(e1/e2) / log(h1/h2)
        rate = math.log(e1 / e2, h1 / h2)
        # Expect rate ≈ 3 (third order) within 20%
        assert rate == pytest.approx(3.0, rel=0.2)
