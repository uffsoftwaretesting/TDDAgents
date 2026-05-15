import pytest
from src.integration import integracao_simpson_1_3


def f_internal_only(x, a, b):
    # Retorna 0 nos extremos (x==a ou x==b) e 1 nos pontos internos
    # Tolerância para float
    if pytest.approx(x, abs=1e-8) == a or pytest.approx(x, abs=1e-8) == b:
        return 0
    return 1


def test_weighted_sum_composite_N4():
    # [0,1], N=4: h=0.25, coeficientes internos: [4,2,4] => soma=10
    # Resultado = 10 * (h/3) = 10 * (0.25/3) = 10/12 = 5/6
    a, b, N = 0.0, 1.0, 4
    result = integracao_simpson_1_3(lambda x: f_internal_only(x, a, b), a, b, N)
    assert isinstance(result, float)
    assert result == pytest.approx(5/6)


def test_weighted_sum_composite_N6():
    # [0,1], N=6: h=1/6, coeficientes internos: [4,2,4,2,4] => soma=16
    # Resultado = 16 * (h/3) = 16 * (1/6/3) = 16/18 = 8/9
    a, b, N = 0.0, 1.0, 6
    result = integracao_simpson_1_3(lambda x: f_internal_only(x, a, b), a, b, N)
    assert isinstance(result, float)
    assert result == pytest.approx(8/9)