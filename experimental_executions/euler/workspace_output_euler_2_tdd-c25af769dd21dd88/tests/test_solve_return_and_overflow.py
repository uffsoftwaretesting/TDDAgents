import math
import pytest
from src.solve import solve


def test_solve_f_returns_non_float_message():
    # f retorna string deve lançar TypeError com mensagem específica
    def f_bad(t, y):
        return "not a float"

    with pytest.raises(TypeError) as excinfo:
        solve(f_bad, 0.0, 1.0, 0.0, 5)
    assert str(excinfo.value) == "f must return a float"


def test_solve_f_returns_inf_propagation():
    # f retorna inf, solve deve retornar inf sem capturar
    def f_inf(t, y):
        return float('inf')

    result = solve(f_inf, 0.0, 1.0, 1.0, 3)
    assert math.isinf(result) and result > 0


def test_solve_f_returns_nan_propagation():
    # f retorna nan, solve deve retornar nan sem capturar
    def f_nan(t, y):
        return float('nan')

    result = solve(f_nan, 0.0, 1.0, 1.0, 4)
    assert math.isnan(result)


def test_solve_f_raises_overflow_error():
    # f dispara OverflowError, solve deve propagar sem captura
    def f_overflow(t, y):
        # math.exp(1000) normalmente lança OverflowError
        return math.exp(1000)

    with pytest.raises(OverflowError):
        solve(f_overflow, 0.0, 1.0, 1.0, 2)


def test_solve_f_raises_value_error():
    # f dispara ValueError, solve deve propagar sem captura
    def f_value_error(t, y):
        # math.log(-1) lança ValueError
        return math.log(-1)

    with pytest.raises(ValueError):
        solve(f_value_error, 0.0, 1.0, 1.0, 2)
