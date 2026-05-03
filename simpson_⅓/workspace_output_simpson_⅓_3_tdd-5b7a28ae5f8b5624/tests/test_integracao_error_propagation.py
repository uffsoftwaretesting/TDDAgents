import pytest
from src.integracao import integracao_simpson_1_3


def test_f_exception_propagation_intermediate_point():
    # Configura intervalo e subdivisões
    a, b, N = 0.0, 2.0, 4
    h = (b - a) / N
    # Escolhe um ponto interno onde a função irá falhar (x = a + h)
    error_x = a + h

    def f(x):
        # Lança exceção apenas no ponto interno especificado
        if x == error_x:
            raise ValueError("intermediate error")
        return x

    with pytest.raises(ValueError) as exc:
        integracao_simpson_1_3(f, a, b, N)
    assert str(exc.value) == "intermediate error"
