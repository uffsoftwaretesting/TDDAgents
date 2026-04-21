import pytest

from src.adams_bashforth_3 import adams_bashforth_3


def test_ab3_f_not_callable_raises_type_error():
    with pytest.raises(TypeError, match="f must be callable"):
        adams_bashforth_3(123, 0.0, 0.0, 1.0, 0.1)


def test_ab3_t0_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError, match="t0 must be a float"):
        adams_bashforth_3(f, "0.0", 0.0, 1.0, 0.1)


def test_ab3_y0_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError, match="y0 must be a float"):
        adams_bashforth_3(f, 0.0, None, 1.0, 0.1)


def test_ab3_t_final_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError, match="t_final must be a float"):
        adams_bashforth_3(f, 0.0, 0.0, [1.0], 0.1)


def test_ab3_h_not_float():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError, match="h must be a float"):
        adams_bashforth_3(f, 0.0, 0.0, 1.0, "0.1")


def test_ab3_h_zero_or_negative():
    f = lambda t, y: 0.0
    with pytest.raises(ValueError, match="Passo h deve ser positivo"):
        adams_bashforth_3(f, 0.0, 0.0, 1.0, 0.0)
    with pytest.raises(ValueError, match="Passo h deve ser positivo"):
        adams_bashforth_3(f, 0.0, 0.0, 1.0, -1.0)


def test_ab3_t_final_less_than_t0():
    f = lambda t, y: 0.0
    with pytest.raises(ValueError, match="t_final deve ser ≥ t0"):
        adams_bashforth_3(f, 1.0, 0.0, 0.5, 0.1)
