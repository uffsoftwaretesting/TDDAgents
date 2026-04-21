import pytest
from src.rk2_heun import rk2_heun


class MyCustomError(Exception):
    """Exceção customizada para testes de propagação."""
    pass


def test_custom_exception_in_k1_is_propagated():
    """
    Se f lançar no primeiro cálculo de k1, rk2_heun deve propagar MyCustomError sem captura.
    """
    def f(t, y):
        raise MyCustomError("error at k1")

    with pytest.raises(MyCustomError) as excinfo:
        rk2_heun(f, 0.0, 0.0, 1.0, 0.5)
    assert str(excinfo.value) == "error at k1"


def test_custom_exception_in_k2_is_propagated():
    """
    Se f lançar no cálculo de k2 (segunda inclinação), rk2_heun deve propagar MyCustomError sem captura,
    e devemos observar que k1 foi chamado antes de k2.
    """
    calls = []

    def f(t, y):
        # Primeiro retorno para k1, depois lança no k2
        if not calls:
            calls.append(("k1", t, y))
            return 2.0
        else:
            calls.append(("k2", t, y))
            raise MyCustomError("error at k2")

    t0 = 0.0
    y0 = 1.0
    h = 0.5
    t_final = t0 + h

    with pytest.raises(MyCustomError) as excinfo:
        rk2_heun(f, t0, y0, t_final, h)

    # A exceção customizada deve ser propagada com a mensagem correta
    assert str(excinfo.value) == "error at k2"
    # Confirma que k1 foi chamado antes de k2
    assert len(calls) == 2
    assert calls[0][0] == "k1"
    assert calls[1][0] == "k2"
    # Verifica argumentos de chamada correspondentes
    assert calls[0][1:] == pytest.approx((t0, y0))
    # Para k2, tempo já avançou em h e y_pred = y0 + h * 2.0
    y_pred = y0 + h * 2.0
    assert calls[1][1:] == pytest.approx((t0 + h, y_pred))
