import pytest
from src.solve import solve

def test_solve_returns_initial_value_when_tf_equals_t0():
    """
    Quando tf == t0, solve deve retornar imediatamente float(y0), sem chamar f.
    """
    # f provocaria erro se fosse chamada
    f = lambda t, y: 1 / 0
    t0 = 1.234
    y0 = -5
    result = solve(f, t0, t0, y0, n=10)
    assert isinstance(result, float), "O retorno deve ser float"
    assert result == float(y0), f"Esperado retorno {float(y0)}, obtido {result}"


def test_solve_does_not_call_f_when_tf_equals_t0():
    """
    Garante que f não seja chamada quando tf == t0.
    """
    calls = []
    def f(t, y):
        calls.append((t, y))
        return 42

    t0 = 0
    y0 = 100
    # mesmo com n grande, não deve ocorrer passagem em f
    result = solve(f, t0, t0, y0, n=1000)
    assert calls == [], f"f foi chamada {len(calls)} vezes quando não deveria"
    assert result == float(y0), f"Esperado retorno {float(y0)}, obtido {result}"