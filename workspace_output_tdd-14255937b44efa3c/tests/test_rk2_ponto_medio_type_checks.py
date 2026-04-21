import pytest

from src.rk2_ponto_medio import rk2_ponto_medio


def test_non_float_k1_raises_type_error():
    """
    Se f retornar valor não-float em k1 (primeira chamada), deve lançar TypeError
    """
    call = {"count": 0}
    def f(t, y):
        call["count"] += 1
        if call["count"] == 1:
            return "not a float"
        # Para não afetar outras etapas
        return 0.0

    with pytest.raises(TypeError):
        rk2_ponto_medio(f, 0.0, 0.0, 0.5, 0.5)


def test_non_float_k2_raises_type_error():
    """
    Se f retornar valor não-float em k2 (segunda chamada), deve lançar TypeError
    """
    call = {"count": 0}
    def f(t, y):
        call["count"] += 1
        if call["count"] == 1:
            # k1 válido
            return 0.0
        if call["count"] == 2:
            # k2 inválido
            return None
        return 0.0

    with pytest.raises(TypeError):
        rk2_ponto_medio(f, 0.0, 1.0, 0.5, 0.5)