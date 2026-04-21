import pytest
import numpy as np
from diferencas_finitas_bvp.core import diferencas_finitas_bvp

# Fonte genérica (não usada nas fronteiras)
def dummy_f(x):
    return np.zeros_like(x)


def test_diferencas_finitas_bvp_boundary_returns_bc():
    """
    x_alvo igual a a ou b deve retornar exatamente os valores de contorno sem chamar internals.
    """
    f = lambda x: np.ones_like(x)
    a, b = 0.0, 1.0
    bc = {"u_a": 2.5, "u_b": -1.5}
    N = 5
    # Testa fronteira esquerda
    result_left = diferencas_finitas_bvp(f, a, b, bc, N, a)
    assert isinstance(result_left, float)
    assert result_left == pytest.approx(bc['u_a'])
    # Testa fronteira direita
    result_right = diferencas_finitas_bvp(f, a, b, bc, N, b)
    assert isinstance(result_right, float)
    assert result_right == pytest.approx(bc['u_b'])


def test_diferencas_finitas_bvp_invalid_parameters_raise_value_error():
    """
    Parâmetros inválidos devem causar ValueError via validação.
    """
    f = lambda x: np.ones_like(x)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    N = 5
    # a não float
    with pytest.raises(ValueError):
        diferencas_finitas_bvp(f, '0.0', b, bc, N, 0.5)
    # bc sem chaves corretas
    with pytest.raises(ValueError):
        diferencas_finitas_bvp(f, a, b, {'u_a':0.0}, N, 0.5)
    # x_alvo fora do intervalo
    with pytest.raises(ValueError):
        diferencas_finitas_bvp(f, a, b, bc, N, -0.1)


def test_diferencas_finitas_bvp_N1_not_implemented():
    """
    Para N=1, _assemble_system lança NotImplementedError e deve propagar.
    """
    f = dummy_f
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    with pytest.raises(NotImplementedError):
        diferencas_finitas_bvp(f, a, b, bc, 1, 0.5)
