import math
import pytest
from src.integration import integracao_simpson_1_3

@pytest.mark.parametrize(
    "N, tol",
    [
        (2, 1e-1),   # tolerância mais larga para N pequeno
        (4, 1e-2),   # melhora para N moderado
        (10, 1e-3),  # N maior, tolerância ajustada para refletir erro esperado
        (100, 1e-6), # N suficientemente grande, precisão apurada
    ]
)
def test_sin_convergence_on_0_to_pi(N, tol):
    """
    Testa convergência da regra de Simpson 1/3 para f(x)=sin(x) em [0, π].
    Integral exata: ∫₀^π sin(x) dx = 2
    """
    result = integracao_simpson_1_3(math.sin, 0, math.pi, N)
    expected = 2.0
    assert isinstance(result, float)
    assert result == pytest.approx(expected, abs=tol)

@pytest.mark.parametrize(
    "N, tol",
    [
        (2, 2e-1),   # exp cresce rápido, erro maior para N=2
        (4, 5e-2),   # N=4, tolerância média
        (10, 1e-3),  # aproximação já razoável
        (100, 1e-6), # alta precisão esperada
    ]
)
def test_exp_convergence_on_0_to_1(N, tol):
    """
    Testa convergência da regra de Simpson 1/3 para f(x)=exp(x) em [0, 1].
    Integral exata: ∫₀¹ exp(x) dx = e - 1
    """
    result = integracao_simpson_1_3(math.exp, 0, 1, N)
    expected = math.e - 1
    assert isinstance(result, float)
    assert result == pytest.approx(expected, abs=tol)