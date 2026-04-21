import pytest
import numpy as np
from diferencas_finitas_bvp.core import diferencas_finitas_bvp


def test_diferencas_finitas_bvp_f_returns_nan():
    """
    Se a função fonte retorna NaN, o algoritmo deve detectar e lançar ValueError.
    """
    f_nan = lambda x: np.full_like(x, np.nan)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    N = 5
    x_alvo = 0.5
    with pytest.raises(ValueError):
        diferencas_finitas_bvp(f_nan, a, b, bc, N, x_alvo)


def test_diferencas_finitas_bvp_f_returns_inf():
    """
    Se a função fonte retorna Inf, o algoritmo deve detectar e lançar ValueError.
    """
    f_inf = lambda x: np.full_like(x, np.inf)
    a, b = 0.0, 1.0
    bc = {"u_a": 0.0, "u_b": 0.0}
    N = 5
    x_alvo = 0.5
    with pytest.raises(ValueError):
        diferencas_finitas_bvp(f_inf, a, b, bc, N, x_alvo)
