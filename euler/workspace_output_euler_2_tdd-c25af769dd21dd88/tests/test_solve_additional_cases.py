import pytest
from fractions import Fraction
from src.solve import solve


def test_solve_f_returns_none_message():
    """
    f retorna None deve lançar TypeError com mensagem específica
    """
    def f_none(t, y):
        return None

    with pytest.raises(TypeError) as excinfo:
        solve(f_none, 0.0, 1.0, 0.0, 5)
    assert str(excinfo.value) == "f must return a float"


def test_solve_f_returns_fraction():
    """
    f retorna Fraction, que é Real, deve ser convertido para float sem erro.
    Para f(t,y)=Fraction(1), y(tf)=y0 + (tf-t0)*1
    """
    def f_frac(t, y):
        return Fraction(1, 1)

    t0 = 2.0
    tf = 5.0
    y0 = 0.5
    n = 3
    result = solve(f_frac, t0, tf, y0, n)
    expected = y0 + (tf - t0) * 1.0
    assert result == pytest.approx(expected)
