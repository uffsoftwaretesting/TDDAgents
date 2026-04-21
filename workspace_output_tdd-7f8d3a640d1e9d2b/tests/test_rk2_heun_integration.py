import pytest
import math
from src.rk2_heun import rk2_heun


def test_integration_constant_derivative_multiple_steps():
    t0 = 0.0
    y0 = 0.0
    k = 3.0
    h = 1.0
    steps = 3
    t_final = t0 + steps * h
    calls = []
    def f(t, y):
        calls.append((t, y))
        return k

    y_result = rk2_heun(f, t0, y0, t_final, h)
    # Para derivada constante RK2 é exato: y_final = y0 + k*(t_final - t0)
    expected_y = y0 + k * (t_final - t0)
    assert y_result == pytest.approx(expected_y)
    # Duas chamadas por passo
    assert len(calls) == 2 * steps
    # Conferir sequência de chamadas (k1 e k2)
    for i in range(steps):
        # k1
        t_i = t0 + i * h
        y_i = y0 + i * k * h
        assert calls[2 * i] == pytest.approx((t_i, y_i))
        # k2
        t_ip1 = t_i + h
        y_pred = y_i + h * k
        assert calls[2 * i + 1] == pytest.approx((t_ip1, y_pred))


def test_integration_exponential_derivative_multiple_steps():
    t0 = 0.0
    y0 = 1.0
    h = 0.5
    steps = 2
    t_final = t0 + steps * h
    calls = []
    def f(t, y):
        calls.append((t, y))
        return y

    y_result = rk2_heun(f, t0, y0, t_final, h)
    # Cálculo manual do RK2 (Heun) para y'=y:
    # passo 1: k1=1.0, y_pred=1.5, k2=1.5, y1=1.625
    y1 = 1.625
    # passo 2: k1=1.625, y_pred2=2.4375, k2=2.4375, y2=2.640625
    expected_y = 2.640625
    assert y_result == pytest.approx(expected_y)
    # Duas chamadas por passo
    assert len(calls) == 2 * steps
    # Conferir valores das chamadas
    assert calls[0] == pytest.approx((0.0, 1.0))
    assert calls[1] == pytest.approx((0.5, 1.5))
    assert calls[2] == pytest.approx((0.5, y1))
    assert calls[3] == pytest.approx((1.0, 2.4375))


def test_integration_constant_derivative_with_remainder():
    """
    Verify that when (t_final - t0) is not an exact multiple of h, the integration
    uses a final remainder step and still produces the correct result and spy calls.
    """
    t0 = 0.0
    y0 = 0.0
    k = 2.0
    h = 0.3
    t_final = 1.0
    calls = []
    def f(t, y):
        calls.append((t, y))
        return k

    # Execute RK2 Heun over non-exact multiple of h
    y_result = rk2_heun(f, t0, y0, t_final, h)
    # Exact solution for constant derivative
    expected_y = y0 + k * (t_final - t0)
    assert y_result == pytest.approx(expected_y)

    # Compute expected number of full steps and remainder
    interval = t_final - t0
    n_full = int(math.floor(interval / h))
    remainder = interval - n_full * h
    expected_steps = n_full + (1 if remainder > 0 else 0)
    # Two calls per step
    assert len(calls) == 2 * expected_steps

    # Validate each call's time and y (including last smaller hi)
    for i in range(expected_steps):
        # Determine hi for this step
        step_h = h if i < n_full else remainder
        t_i = t0 + i * h
        y_i = y0 + k * (i * h)
        # k1 at (t_i, y_i)
        assert calls[2 * i] == pytest.approx((t_i, y_i))
        # k2 at (t_i + step_h, y_pred)
        y_pred = y_i + step_h * k
        t_pred = t_i + step_h
        assert calls[2 * i + 1] == pytest.approx((t_pred, y_pred))
