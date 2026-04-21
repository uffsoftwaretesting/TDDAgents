import pytest
from src.adams_bashforth_2 import adams_bashforth_2


def test_f_not_callable_raises_type_error():
    """
    Deve lançar TypeError se f não for callable.
    """
    with pytest.raises(TypeError) as excinfo:
        adams_bashforth_2(5, 0.0, 1.0, 1.0, 0.1)
    assert "f must be Callable" in str(excinfo.value)


def test_t0_not_float_raises_type_error():
    """
    Deve lançar TypeError se t0 não for float.
    """
    with pytest.raises(TypeError) as excinfo:
        adams_bashforth_2(lambda t, y: y, "0", 1.0, 1.0, 0.1)
    assert "t0 must be float" in str(excinfo.value)


def test_y0_not_float_raises_type_error():
    """
    Deve lançar TypeError se y0 não for float.
    """
    with pytest.raises(TypeError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 0.0, "1.0", 1.0, 0.1)
    assert "y0 must be float" in str(excinfo.value)


def test_t_eval_not_float_raises_type_error():
    """
    Deve lançar TypeError se t_eval não for float.
    """
    with pytest.raises(TypeError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 0.0, 1.0, "1.0", 0.1)
    assert "t_eval must be float" in str(excinfo.value)


def test_h_not_float_raises_type_error():
    """
    Deve lançar TypeError se h não for float.
    """
    with pytest.raises(TypeError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 0.0, 1.0, 1.0, "0.1")
    assert "h must be float" in str(excinfo.value)


def test_h_le_zero_raises_value_error():
    """
    Deve lançar ValueError se h for menor ou igual a zero.
    """
    with pytest.raises(ValueError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 0.0, 1.0, 1.0, 0.0)
    assert "h must be greater than zero" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 0.0, 1.0, 1.0, -0.1)
    assert "h must be greater than zero" in str(excinfo.value)


def test_t_eval_lt_t0_raises_value_error():
    """
    Deve lançar ValueError se t_eval for menor que t0.
    """
    with pytest.raises(ValueError) as excinfo:
        adams_bashforth_2(lambda t, y: y, 1.0, 1.0, 0.5, 0.1)
    assert "t_eval must be greater than or equal to t0" in str(excinfo.value)


def test_delta_zero_returns_initial_value():
    """
    Deve retornar o valor inicial y0 quando t_eval == t0 (Δ == 0) sem chamar f.
    """
    t0 = 2.0
    y0 = 5.0
    t_eval = t0
    h = 0.1
    def f(t, y):
        raise AssertionError("f should not be called for Δ==0")
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    assert result == y0


def test_integer_division_h_divides_delta_constant_derivative():
    """
    Quando h divide exatamente delta, n_steps = delta/h e h recalibrado = h.
    Para derivada constante f=1, y(t_eval) = y0 + delta.
    """
    t0 = 0.0
    y0 = 2.0
    t_eval = 1.0
    h = 0.25  # delta = 1.0, delta/h = 4
    f = lambda t, y: 1.0
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    assert result == pytest.approx(y0 + (t_eval - t0))


def test_non_integer_division_h_adjustment_constant_derivative():
    """
    Quando h não divide delta, n_steps = ceil(delta/h), h recalibrado = delta/n_steps.
    Para derivada constante f=1, y(t_eval) = y0 + delta independentemente de h inicial.
    """
    t0 = 1.0
    y0 = 0.5
    t_eval = 2.3  # delta = 1.3
    h = 0.4       # delta/h = 3.25 => n_steps = 4, h_recalibrado = 0.325
    f = lambda t, y: 1.0
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    assert result == pytest.approx(y0 + (t_eval - t0))


def test_single_euler_start_step_reduces_to_explicit_euler():
    """
    Quando delta < h, n_steps = 1 e método deve ser um único passo de Euler explícito.
    t1 = t0 + delta, y1 = y0 + delta * f(t0, y0).
    """
    t0 = 1.0
    y0 = 2.0
    c = 3.5
    t_eval = 1.1  # delta = 0.1
    h = 0.2       # delta/h = 0.5 => n_steps = 1, h_recalibrado = 0.1
    f = lambda t, y: c
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    expected = y0 + c * (t_eval - t0)
    assert result == pytest.approx(expected)


def test_adams_bashforth_2_linear_ode_two_steps_ab2_intermediate_and_final():
    """
    Para EDO linear dy/dt = λ y, verifica iteração AB2 com n_steps=2.
    """
    t0 = 0.0
    y0 = 1.0
    lam = 1.0
    f = lambda t, y: lam * y
    t_eval = 0.8  # delta = 0.8, h = 0.4 => n_steps = 2
    h = 0.4
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    # Euler: y1 = 1.0 + 0.4*1.0 = 1.4
    # AB2:   y2 = 1.4 + 0.4*(1.5*1.4 - 0.5*1.0) = 2.04
    expected = 2.04
    assert result == pytest.approx(expected)


def test_adams_bashforth_2_linear_ode_three_steps_ab2_final():
    """
    Para EDO linear dy/dt = λ y, verifica iteração AB2 com n_steps=3.
    """
    t0 = 0.0
    y0 = 1.0
    lam = 1.0
    f = lambda t, y: lam * y
    t_eval = 1.2  # delta = 1.2, h = 0.4 => n_steps = 3
    h = 0.4
    result = adams_bashforth_2(f, t0, y0, t_eval, h)
    # Euler:          y1 = 1.0 + 0.4*1.0 = 1.4
    # AB2 step 2:     y2 = 1.4 + 0.4*(1.5*1.4 - 0.5*1.0) = 2.04
    # AB2 step 3:     y3 = 2.04 + 0.4*(1.5*2.04 - 0.5*1.4) = 2.984
    expected = pytest.approx(2.984)
    assert result == expected
