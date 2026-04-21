import pytest
import math

from src.adams_bashforth_3 import adams_bashforth_3

@ pytest.mark.parametrize("h,tol", [
    (0.1, 1e-2),    # passo relativamente grosso, tolerância suave
    (0.05, 1e-3),   # passo intermediário, tolerância intermediária
    (0.01, 1e-6),   # passo fino, tolerância rigorosa
])
def test_exponential_integration_accuracy(h, tol):
    """
    Integração de y' = y, y(0)=1 até t=1.0. Solução exata y(1)=exp(1).
    Verifica que |aprox - exato| <= tol para diferentes tamanhos de passo.
    """
    t0 = 0.0
    y0 = 1.0
    t_final = 1.0
    f = lambda t, y: y

    approx = adams_bashforth_3(f, t0, y0, t_final, float(h))
    exact = math.exp(t_final) * y0

    # Verifica erro absoluto dentro da tolerância ajustada
    assert approx == pytest.approx(exact, abs=tol)
