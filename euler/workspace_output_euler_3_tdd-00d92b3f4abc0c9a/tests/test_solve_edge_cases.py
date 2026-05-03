import math
import pytest

from src.solve import solve


def test_constant_backward_interval():
    """
    Para f constante e tf < t0, o resultado deve ser y0 + c*(tf - t0).
    """
    t0, tf, y0, c, n = 5.0, 1.0, 1.0, 2.0, 4
    f = lambda t, y: c
    result = solve(f, t0, tf, y0, n)
    expected = y0 + c * (tf - t0)
    assert result == pytest.approx(expected, rel=1e-12), (
        f"Esperado {expected} para intervalo reverso, obteve {result}"
    )


def test_propagates_inf_from_f():
    """
    Se f retornar inf, o solver deve propagar inf na saída.
    """
    f = lambda t, y: float('inf')
    result = solve(f, 0.0, 1.0, 0.0, 10)
    assert result == float('inf'), "Solver deve retornar inf quando f retorna inf"


def test_propagates_nan_from_f():
    """
    Se f retornar nan, o solver deve propagar nan na saída.
    """
    f = lambda t, y: float('nan')
    result = solve(f, 0.0, 1.0, 1.0, 5)
    assert math.isnan(result), "Solver deve retornar nan quando f retorna nan"