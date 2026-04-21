import math
import pytest
from taylor_2.taylor_2 import taylor_2


def test_exception_propagated_when_f_raises_zero_division():
    def f(t, y):
        return 1/0  # ZeroDivisionError
    def df(t, y):
        return 0.0
    with pytest.raises(ZeroDivisionError):
        taylor_2(f, df, t0=0.0, y0=1.0, t_final=0.1, h=0.1)


def test_exception_propagated_when_df_raises_zero_division():
    def f(t, y):
        return 0.0
    def df(t, y):
        return 1/0  # ZeroDivisionError
    with pytest.raises(ZeroDivisionError):
        taylor_2(f, df, t0=0.0, y0=1.0, t_final=0.1, h=0.1)


@ pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_runtime_error_when_f_returns_non_finite(value):
    def f(t, y):
        return value
    def df(t, y):
        return 0.0
    with pytest.raises(RuntimeError) as exc:
        taylor_2(f, df, t0=0.0, y0=1.0, t_final=0.1, h=0.1)
    assert "Divergência detectada: valor não finito" in str(exc.value)


@ pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_runtime_error_when_df_returns_non_finite(value):
    def f(t, y):
        return 0.0
    def df(t, y):
        return value
    with pytest.raises(RuntimeError) as exc:
        taylor_2(f, df, t0=0.0, y0=1.0, t_final=0.1, h=0.1)
    assert "Divergência detectada: valor não finito" in str(exc.value)