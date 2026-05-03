import pytest
from src.solver import adams_bashforth_3

def test_single_step_rk4_constant_derivative():
    """
    Se t_final - t0 < h, deve usar um único passo RK4 e retornar o valor correto
    para a ODE dy/dt = C.
    """
    C = 3.0
    def f(t, y):
        return C
    t0 = 1.0
    y0 = 2.0
    t_final = 1.2  # dt = 0.2 < h
    h = 0.5
    # RK4 com derivada constante é exato: y = y0 + C * dt
    expected = y0 + C * (t_final - t0)
    y = adams_bashforth_3(f, t0, y0, t_final, h)
    assert pytest.approx(y, rel=1e-12) == expected

def test_single_step_rk4_exponential():
    """
    Se t_final - t0 < h, deve usar um único passo RK4 para dy/dt = y,
    comparando com implementação local de RK4.
    """
    def f(t, y):
        return y
    t0 = 0.0
    y0 = 1.0
    t_final = 0.1  # dt = 0.1 < h
    h = 0.2

    # Implementação local de um passo RK4
    def rk4_step(f, t, y, dt):
        k1 = f(t, y)
        k2 = f(t + dt/2, y + dt/2 * k1)
        k3 = f(t + dt/2, y + dt/2 * k2)
        k4 = f(t + dt, y + dt * k3)
        return y + dt/6 * (k1 + 2*k2 + 2*k3 + k4)

    expected = rk4_step(f, t0, y0, t_final - t0)
    y = adams_bashforth_3(f, t0, y0, t_final, h)
    assert pytest.approx(y, rel=1e-12) == expected
