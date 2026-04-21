import math
import pytest
from taylor_2.taylor_2 import taylor_2

@ pytest.mark.parametrize(
    "k, y0, t0, t_final, h",
    [
        (1.0, 2.0, 0.0, 1.0, 0.1),   # domínio inicial t0=0
        (1.0, 2.0, 0.0, 1.0, 0.05),  # passo menor
        (1.0, 3.0, 1.0, 2.0, 0.1),   # t0 não-zero
    ]
)
def test_taylor2_exponential_decay(k, y0, t0, t_final, h):
    """
    Integração da EDO y' = -k*y com a solução analítica y = y0 * exp(-k*(t_final - t0)).
    Compara a aproximação de taylor_2 para diferentes passos.
    """
    # Definição das derivadas
    def f(t, y):
        return -k * y

    def df(t, y):
        # df = d/dt f = ∂f/∂t + ∂f/∂y * f = 0 + (-k) * (-k*y) = k**2 * y
        return k * k * y

    approx = taylor_2(f, df, t0, y0, t_final, h)
    expected = y0 * math.exp(-k * (t_final - t0))
    # Comportamento de ordem 2: erro O(h^2). Usamos tolerância relativa de 1e-2.
    assert pytest.approx(expected, rel=1e-2) == approx
    # A solução deve sempre decrescer para k>0
    assert approx < y0
