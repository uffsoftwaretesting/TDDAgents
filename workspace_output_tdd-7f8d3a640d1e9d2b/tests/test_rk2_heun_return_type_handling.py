import pytest
from src.rk2_heun import rk2_heun
from decimal import Decimal


def test_f_returns_int_or_decimal_and_is_converted_to_float():
    """
    When f returns an int or Decimal, rk2_heun should convert it to float and compute correctly.
    """
    for ret in [2, Decimal("3.0")]:
        c = ret
        t0 = 0.0
        y0 = 1.0
        h = 0.5
        t_final = t0 + h
        def f(t, y):
            # ensure inputs are floats
            assert isinstance(t, float)
            assert isinstance(y, float)
            return c
        # expected y_next = y0 + h * float(c)
        expected_y = y0 + h * float(c)
        y_result = rk2_heun(f, t0, y0, t_final, h)
        assert isinstance(y_result, float)
        assert y_result == pytest.approx(expected_y)


def test_f_returns_unconvertible_object_raises_type_error():
    """
    If f returns an object that cannot be converted to float, a TypeError should be raised.
    """
    class Dummy:
        pass

    def f(t, y):
        return Dummy()

    with pytest.raises(TypeError):
        rk2_heun(f, 0.0, 0.0, 0.5, 0.1)


def test_exception_from_f_is_propagated():
    """
    Any exception raised inside f should propagate unchanged.
    """
    def f(t, y):
        raise ValueError("custom error")

    with pytest.raises(ValueError) as excinfo:
        rk2_heun(f, 0.0, 0.0, 1.0, 0.5)
    assert str(excinfo.value) == "custom error"