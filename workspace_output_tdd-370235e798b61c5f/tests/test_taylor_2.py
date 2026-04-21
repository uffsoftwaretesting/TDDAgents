import math
import pytest
from taylor_2.taylor_2 import taylor_2


def test_taylor2_returns_y0_and_no_calls_when_tfinal_equals_t0():
    t0 = 1.0
    y0 = 2.5
    t_final = 1.0
    h = 0.1
    called = {"f": False, "df": False}

    def f(t, y):
        called["f"] = True
        return 0.0

    def df(t, y):
        called["df"] = True
        return 0.0

    result = taylor_2(f, df, t0, y0, t_final, h)
    assert result == y0
    assert not called["f"], "f should not be called when t_final == t0"
    assert not called["df"], "df should not be called when t_final == t0"


def test_raise_value_error_when_tfinal_less_than_t0():
    f = lambda t, y: 0.0
    df = lambda t, y: 0.0
    with pytest.raises(ValueError) as exc:
        taylor_2(f, df, t0=2.0, y0=0.0, t_final=1.0, h=0.1)
    assert "t_final deve ser ≥ t0" in str(exc.value)


def test_raise_value_error_when_h_non_positive():
    f = lambda t, y: 0.0
    df = lambda t, y: 0.0
    for bad_h in [0, -0.5]:
        with pytest.raises(ValueError) as exc:
            taylor_2(f, df, t0=0.0, y0=1.0, t_final=1.0, h=bad_h)
        assert "h deve ser > 0 e finito" in str(exc.value)


def test_raise_value_error_when_h_not_finite():
    f = lambda t, y: 0.0
    df = lambda t, y: 0.0
    for bad_h in [math.nan, math.inf, -math.inf]:
        with pytest.raises(ValueError) as exc:
            taylor_2(f, df, t0=0.0, y0=1.0, t_final=1.0, h=bad_h)
        assert "h deve ser > 0 e finito" in str(exc.value)


def test_raise_type_error_when_f_not_callable():
    df = lambda t, y: 0.0
    with pytest.raises(TypeError) as exc:
        taylor_2(f=None, df=df, t0=0.0, y0=0.0, t_final=1.0, h=0.1)
    assert "f e df devem ser callables puros" in str(exc.value)


def test_raise_type_error_when_df_not_callable():
    f = lambda t, y: 0.0
    with pytest.raises(TypeError) as exc:
        taylor_2(f=f, df=123, t0=0.0, y0=0.0, t_final=1.0, h=0.1)
    assert "f e df devem ser callables puros" in str(exc.value)
