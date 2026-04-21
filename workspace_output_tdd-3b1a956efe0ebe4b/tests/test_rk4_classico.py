import pytest
from src.rk4_classico import rk4_classico


def test_f_not_callable_raises_type_error():
    with pytest.raises(TypeError):
        rk4_classico(5, 0.0, 1.0, 2.0, 0.1)


def test_t0_not_float_raises_type_error():
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: t + y, '0.0', 1.0, 2.0, 0.1)


def test_y0_not_float_raises_type_error():
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: t + y, 0.0, '1.0', 2.0, 0.1)


def test_t_final_not_float_raises_type_error():
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: t + y, 0.0, 1.0, '2.0', 0.1)


def test_h_not_float_raises_type_error():
    with pytest.raises(TypeError):
        rk4_classico(lambda t, y: t + y, 0.0, 1.0, 2.0, '0.1')


def test_h_less_or_equal_zero_raises_value_error():
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: t + y, 0.0, 1.0, 2.0, 0.0)
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: t + y, 0.0, 1.0, 2.0, -0.1)


def test_t_final_not_greater_than_t0_raises_value_error():
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: t + y, 2.0, 1.0, 2.0, 0.1)
    with pytest.raises(ValueError):
        rk4_classico(lambda t, y: t + y, 2.0, 1.0, 1.5, 0.1)


@pytest.mark.parametrize("param, invalid_value", [
    ("t0", 0),
    ("y0", 1),
    ("t_final", 2),
    ("h", 1)
])
def test_int_not_float_raises_type_error(param, invalid_value):
    """
    Passing an int for any of t0, y0, t_final, or h should raise a TypeError
    because only float is accepted.
    """
    kwargs = {
        "f": lambda t, y: t + y,
        "t0": 0.0,
        "y0": 1.0,
        "t_final": 2.0,
        "h": 0.1
    }
    # Inject the invalid int value into the respective parameter
    kwargs[param] = invalid_value  # ints are not floats
    with pytest.raises(TypeError):
        rk4_classico(**kwargs)