import pytest
from taylor_2.taylor_2 import taylor_2

def test_last_step_with_nonzero_second_derivative():
    # Configuração do problema
    t0 = 0.0
    y0 = 0.0
    t_final = 0.35  # 3 passos de 0.1 = 0.3 e resto de 0.05
    h = 0.1
    # Derivadas constantes
    c = 1.0  # f(t, y) = c
    d = 2.0  # df(t, y) = d
    calls = {"f": 0, "df": 0}

    def f(t, y):
        calls["f"] += 1
        return c

    def df(t, y):
        calls["df"] += 1
        return d

    # Execução
    result = taylor_2(f, df, t0, y0, t_final, h)

    # Verifica número de chamadas (3 full steps + 1 last step)
    assert calls["f"] == 4, f"Expected 4 calls to f, got {calls['f']}"
    assert calls["df"] == 4, f"Expected 4 calls to df, got {calls['df']}"

    # Cálculo esperado:
    # full steps (n=3): y_full = y0 + n*h*c + n*(h^2/2)*d
    n = 3
    r = t_final - (t0 + n * h)
    y_full = y0 + n * h * c + n * (h * h / 2) * d
    # last step: y_last = y_full + r*c + (r^2/2)*d
    expected = y_full + r * c + (r * r / 2) * d
    assert pytest.approx(result, rel=1e-12) == expected
