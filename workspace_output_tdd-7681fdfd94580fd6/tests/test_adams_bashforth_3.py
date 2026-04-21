import pytest
from src.adams_bashforth_3 import adams_bashforth_3, _validate_args


def test_n_steps_zero_returns_y0():
    # Para t_final == t0, sem passos, deve retornar y0
    f = lambda t, y: 0.0
    t0 = 0.0
    y0 = 2.5
    t_final = 0.0
    h = 0.1
    assert adams_bashforth_3(f, t0, y0, t_final, h) == y0


def test_validate_args_f_not_callable():
    with pytest.raises(TypeError):
        _validate_args(123, 0.0, 0.0, 1.0, 0.1)


def test_validate_args_t0_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError):
        _validate_args(f, '0.0', 0.0, 1.0, 0.1)


def test_validate_args_y0_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError):
        _validate_args(f, 0.0, None, 1.0, 0.1)


def test_validate_args_t_final_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError):
        _validate_args(f, 0.0, 0.0, [1.0], 0.1)


def test_validate_args_h_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError):
        _validate_args(f, 0.0, 0.0, 1.0, '0.1')


def test_validate_args_h_zero_or_negative():
    f = lambda t, y: 0.0
    # h == 0
    with pytest.raises(ValueError, match="Passo h deve ser positivo"):
        _validate_args(f, 0.0, 0.0, 1.0, 0.0)
    # h < 0
    with pytest.raises(ValueError, match="Passo h deve ser positivo"):
        _validate_args(f, 0.0, 0.0, 1.0, -0.5)


def test_validate_args_t_final_less_than_t0():
    f = lambda t, y: 0.0
    with pytest.raises(ValueError, match="t_final deve ser ≥ t0"):
        _validate_args(f, 1.0, 0.0, 0.5, 0.1)

# Sub-requisito: n_steps calculation integrity

def test_n_steps_not_integer_raises_value_error():
    # (t_final - t0)/h tem parte fracionária grande -> erro
    f = lambda t, y: 0.0
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + 2.3 * h
    with pytest.raises(ValueError, match="Número de passos não inteiro"):
        adams_bashforth_3(f, t0, y0, t_final, h)

# Casos base Euler: n_steps == 1 e n_steps == 2

def test_n_steps_one_constant_function():
    f = lambda t, y: 2.0
    t0 = 0.0
    y0 = 1.0
    h = 0.5
    t_final = t0 + 1 * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + h * 2.0
    assert result == expected


def test_n_steps_two_constant_function():
    f = lambda t, y: -1.5
    t0 = 1.0
    y0 = 2.0
    h = 1.0
    t_final = t0 + 2 * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 + 2 * h * (-1.5)
    assert result == expected


def test_n_steps_one_y_function():
    f = lambda t, y: y
    t0 = 0.0
    y0 = 1.0
    h = 0.1
    t_final = t0 + h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    expected = y0 * (1 + h)
    assert result == pytest.approx(expected)


def test_n_steps_two_y_function():
    f = lambda t, y: y
    t0 = -1.0
    y0 = 2.0
    h = 0.2
    t_final = t0 + 2 * h
    result = adams_bashforth_3(f, t0, y0, t_final, h)
    y1 = y0 + h * y0
    y2 = y1 + h * y1
    expected = y2
    assert result == pytest.approx(expected)
