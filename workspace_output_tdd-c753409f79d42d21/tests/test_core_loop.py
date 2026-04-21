import pytest
from src.solver_euler import euler_explicito


def test_exponential_growth_doubling():
    """
    For dy/dt = y, with h=1, the explicit Euler method yields
    y_n = y0*(1+1)^n = y0*2^n
    """
    t0 = 0.0
    y0 = 2.0
    n = 5
    h = 1.0
    t_final = t0 + n * h
    result = euler_explicito(lambda t, y: y, t0, y0, t_final, h)
    expected = y0 * (2 ** n)
    assert result == pytest.approx(expected)


def test_constant_derivative():
    """
    For dy/dt = c, the explicit Euler method yields
    y_n = y0 + c*(t_final - t0)
    """
    t0 = 0.0
    y0 = 1.0
    c = 3.5
    h = 0.25
    t_final = t0 + 1.0  # so N = 4 steps
    result = euler_explicito(lambda t, y: c, t0, y0, t_final, h)
    expected = y0 + c * (t_final - t0)
    assert result == pytest.approx(expected)


def test_derivative_called_expected_number_of_times_and_arguments():
    """
    The derivative function should be called exactly N times,
    with t values starting from t0, incremented by h each step,
    and y values updated accordingly (here kept constant).
    """
    t0 = 1.0
    y0 = -2.0
    h = 0.5
    t_final = t0 + 2.0  # N = 4 steps
    ts = []
    ys = []

    def f(t, y):
        ts.append(t)
        ys.append(y)
        return 0.0  # keep y constant

    result = euler_explicito(f, t0, y0, t_final, h)
    N = int(round((t_final - t0) / h))

    # result should equal the unchanged initial value
    assert result == pytest.approx(y0)

    # derivative should be called exactly N times
    assert len(ts) == N
    assert len(ys) == N

    # verify arguments passed to f
    for i in range(N):
        assert ts[i] == pytest.approx(t0 + i * h)
        assert ys[i] == pytest.approx(y0)
