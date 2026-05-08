import math
import pytest
from src.solver import adams_bashforth_3

@pytest.mark.parametrize("t0, y0, t_final, h", [
    # passo uniforme múltiplos exatos de h
    (0.0, 1.0, 1.0, 0.1),
    (0.0, 1.0, 2.5, 0.2),
    # passo final menor que h em multistep
    (0.5, 1.0, 3.3, 0.3),
    # única etapa RK4 (t_final - t0 < h)
    (1.0, 1.0, 1.05, 0.1),
    # multistep com último passo dt_extra < h
    (2.0, 1.0, 3.7, 0.5),
])
def test_acceptance_exponential_growth(t0, y0, t_final, h):
    """
    Teste de aceitação para ODE y' = y, solução y = y0 * exp(t - t0).
    Compara y_final com y0 * exp(t_final - t0) dentro de abs_tol=1e-1.
    """
    # Define a ODE y' = y
    def f(t, y):
        return y

    # Executa o solver
    y_final = adams_bashforth_3(f, float(t0), float(y0), float(t_final), float(h))

    # Solução analítica
    expected = y0 * math.exp(t_final - t0)

    # Verifica dentro de tolerância absoluta realista para AB3
    assert y_final == pytest.approx(expected, abs=1e-1), (
        f"Para t0={t0}, y0={y0}, t_final={t_final}, h={h}: esperava {expected}, obteve {y_final}"
    )