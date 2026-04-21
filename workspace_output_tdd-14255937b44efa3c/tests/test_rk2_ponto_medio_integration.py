import pytest

from src.rk2_ponto_medio import rk2_ponto_medio

@ pytest.mark.parametrize(
    "lam, t0, y0, t_final, h",
    [
        (2.0, 0.0, 1.0, 1.0, 0.1),        # passo exato
        (2.0, 0.0, 1.0, 1.0, 0.3),        # último passo ajustado
        (0.0, 0.0, 5.0, 2.0, 0.7),        # λ=0, y constante
        (-1.5, 1.0, 2.0, 3.5, 0.4),       # intervalo deslocado, passo não divisor exato
    ]
)
def test_rk2_ponto_medio_constant_function(lam, t0, y0, t_final, h):
    """
    Integração de dy/dt = λ com solução analítica y = y0 + λ*(t_final - t0).
    Compara resultado de rk2_ponto_medio com o valor exato dentro de tolerância.
    """
    # Define função constante f
    def f_constant(t, y):
        return lam

    # Valor analítico esperado
    expected = y0 + lam * (t_final - t0)

    # Executa RK2 ponto-médio
    result = rk2_ponto_medio(f_constant, t0, y0, t_final, h)

    # Verifica proximidade com a solução exata
    assert result == pytest.approx(expected, rel=1e-6, abs=1e-8), \
        f"Esperado {expected} mas obteve {result} para λ={lam}, t0={t0}, y0={y0}, t_final={t_final}, h={h}"