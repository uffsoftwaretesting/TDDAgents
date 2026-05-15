import pytest
from src.solver import adams_bashforth_3

def test_integration_reaches_exact_t_final_with_last_small_step():
    """
    Integração completa com múltiplos passos:
    - t_final - t0 > h para usar AB3
    - verifica que o loop atinge exatamente t_final no último passo (dt < h)
    - como f retorna zero, o valor de y não muda
    """
    times = []
    def f(t, y):
        # registra todos os instantes de avaliação
        times.append(t)
        return 0.0

    t0 = 0.0
    y0 = 1.0
    h = 0.3
    # Define um t_final que não seja múltiplo de h para forçar um último passo menor
    t_final = 0.85  # dois passos completos (0.3,0.3) + último passo 0.25

    y = adams_bashforth_3(f, t0, y0, t_final, h)
    # Com derivada zero, y deve permanecer igual a y0
    assert y == pytest.approx(y0)

    # O solver deve ter avaliado f em t_final exatamente no último passo
    assert pytest.approx(max(times), rel=1e-12) == t_final
    # Nenhum tempo registrado deve exceder t_final
    assert all(t <= t_final for t in times)
