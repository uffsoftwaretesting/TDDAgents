import pytest
from src.adams import adams_bashforth_3


def test_adams_returns_y0_when_tfinal_eq_t0():
    # Caso trivial: retorna y0 quando t_final == t0
    assert adams_bashforth_3(lambda t, y: t + y, 0.0, 1.0, 0.0, 0.1) == 1.0


def test_adams_typeerror_when_f_not_callable():
    # f deve ser callable
    with pytest.raises(TypeError):
        adams_bashforth_3(123, 0.0, 1.0, 1.0, 0.1)


@ pytest.mark.parametrize("arg, value", [
    ("t0", "0.0"),    # string
    ("t0", 1),          # int
    ("y0", 1),          # int
    ("t_final", None),  # None
    ("t_final", 2),     # int
    ("h", []),          # list
    ("h", 1)            # int
])
def test_adams_typeerror_when_params_not_float(arg, value):
    # t0, y0, t_final, h devem ser floats
    kwargs = {'f': lambda t, y: t + y, 't0': 0.0, 'y0': 1.0, 't_final': 1.0, 'h': 0.1}
    kwargs[arg] = value
    with pytest.raises(TypeError):
        adams_bashforth_3(**kwargs)


def test_adams_valueerror_when_h_not_positive():
    # h deve ser > 0
    with pytest.raises(ValueError):
        adams_bashforth_3(lambda t, y: t + y, 0.0, 1.0, 1.0, 0.0)
    with pytest.raises(ValueError):
        adams_bashforth_3(lambda t, y: t + y, 0.0, 1.0, 1.0, -0.1)


def test_adams_valueerror_when_tfinal_less_than_t0():
    # t_final deve ser >= t0
    with pytest.raises(ValueError):
        adams_bashforth_3(lambda t, y: t + y, 1.0, 1.0, 0.0, 0.1)

# Helpers para cálculo de RK4 local
def rk4_step(f, t, y, dt):
    k1 = f(t, y)
    k2 = f(t + dt/2, y + dt*k1/2)
    k3 = f(t + dt/2, y + dt*k2/2)
    k4 = f(t + dt, y + dt*k3)
    return y + dt*(k1 + 2*k2 + 2*k3 + k4)/6


def test_adams_single_rk4_step_for_delta_lt_h():
    # Δ < h: um passo único RK4 com dt = Δ
    f = lambda t, y: t + y
    t0, y0 = 0.0, 1.0
    t_final = 0.05  # Δ = 0.05 < h
    h = 0.1
    expected = rk4_step(f, t0, y0, t_final - t0)
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    assert result == pytest.approx(expected, rel=1e-8)


def test_adams_two_rk4_steps_for_h_le_delta_lt_2h():
    # h ≤ Δ < 2h: dois passos RK4 (h e Δ - h)
    f = lambda t, y: t + y
    t0, y0 = 0.0, 1.0
    h = 0.1
    t_final = 0.15  # Δ = 0.15, entre h e 2h

    # Primeiro passo com dt = h
    y1 = rk4_step(f, t0, y0, h)
    # Segundo passo com dt = Δ - h
    dt2 = (t_final - t0) - h
    expected = rk4_step(f, t0 + h, y1, dt2)

    result = adams_bashforth_3(f, t0, y0, t_final, h)
    assert result == pytest.approx(expected, rel=1e-8)

# Novo teste: Δ == h deve render o mesmo que um único passo RK4 de dt=h

def test_adams_two_rk4_steps_for_delta_eq_h():
    f = lambda t, y: t + y
    t0, y0 = 1.0, 2.0
    h = 0.2
    t_final = t0 + h  # Δ = h
    # Primeiro passo com dt = h
    y1 = rk4_step(f, t0, y0, h)
    # Segundo passo com dt = 0 → retorna y1
    expected = y1
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    assert result == pytest.approx(expected, rel=1e-8)

# Novo teste: Δ == 2h deve lançar NotImplementedError

def test_adams_not_implemented_for_delta_eq_2h():
    f = lambda t, y: t + y
    t0, y0 = 0.0, 1.0
    h = 0.1
    t_final = t0 + 2*h  # Δ = 2h
    with pytest.raises(NotImplementedError):
        adams_bashforth_3(f, t0, y0, t_final, h)
