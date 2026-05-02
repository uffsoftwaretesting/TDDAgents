import pytest

from src.adams import _rk4_step

@ pytest.mark.parametrize("c, y0, t0, dt, expected", [
    (0.0, 5.0, 1.0, 2.0, 5.0),         # f ≡ 0 → y unchanged
    (1.0, 2.5, 0.0, 0.4, 2.5 + 1.0 * 0.4),  # f ≡ 1 → y + dt
    (3.5, -1.0, 2.0, 0.2, -1.0 + 3.5 * 0.2), # f ≡ 3.5 → y + 3.5·dt
])
def test_rk4_step_constant(c, y0, t0, dt, expected):
    """
    For a constant derivative f(t,y)=c, the RK4 step is exact: y + c*dt.
    """
    f = lambda t, y: c
    result = _rk4_step(f, t0, y0, dt)
    assert result == pytest.approx(expected, rel=1e-12)


def test_rk4_step_linear_in_t():
    """
    For f(t,y)=2*t the exact integral over dt is y + 2*t*dt + dt**2.
    RK4 integrates polynomials up to degree 3 exactly.
    """
    f = lambda t, y: 2 * t
    y0 = 1.0
    t0 = 3.0
    dt = 0.4
    expected = y0 + 2 * t0 * dt + dt * dt
    result = _rk4_step(f, t0, y0, dt)
    assert result == pytest.approx(expected, rel=1e-12)
