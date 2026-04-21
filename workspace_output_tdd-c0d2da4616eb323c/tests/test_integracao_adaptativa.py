import pytest
import math
from src.integracao_trapezio import integracao_trapezio


def test_integracao_adaptativa_linear_converge():
    """
    Para f(x)=x no intervalo [0,1], a integral é 0.5.
    A integração adaptativa deve convergir para 0.5 dentro da tol especificada.
    """
    f = lambda x: x
    result = integracao_trapezio(f, 0.0, 1.0, tol=1e-6)
    assert result == pytest.approx(0.5, rel=1e-6)


def test_integracao_adaptativa_quadratica_converge():
    """
    Para f(x)=x**2 no intervalo [0,1], a integral exata é 1/3.
    A integração adaptativa deve convergir para ~0.333333 dentro da tol.
    """
    f = lambda x: x**2
    exact = 1.0/3.0
    result = integracao_trapezio(f, 0.0, 1.0, tol=1e-6)
    assert result == pytest.approx(exact, rel=1e-6)


def test_integracao_adaptativa_seno_converge():
    """
    Para f(x)=sin(x) no intervalo [0, pi], a integral exata é 2.
    A integração adaptativa deve convergir para ~2 dentro da tol.
    """
    f = math.sin
    result = integracao_trapezio(f, 0.0, math.pi, tol=1e-6)
    assert result == pytest.approx(2.0, rel=1e-6)


def test_integracao_adaptativa_max_iter_excedido_gera_ValueError():
    """
    Se a tol for irrealisticamente pequena, não deve convergir em até 20 iterações.
    Deve levantar ValueError com mensagem apropriada.
    """
    f = lambda x: x
    with pytest.raises(ValueError) as excinfo:
        integracao_trapezio(f, 0.0, 1.0, tol=1e-20)
    assert str(excinfo.value) == "Não convergiu em até 20 iterações"