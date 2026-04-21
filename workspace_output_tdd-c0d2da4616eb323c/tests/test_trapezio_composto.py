import pytest
from src.integracao_trapezio import _trapezio_composto

@ pytest.mark.parametrize("N", [1, 2, 5, 10])
def test_trapezio_composto_linear_exact(N):
    """
    Para f(x)=x no intervalo [0,1], a integral é 0.5 e a regra do trapézio composto deve ser exata para qualquer N.
    """
    f = lambda x: x
    a, b = 0.0, 1.0
    result = _trapezio_composto(f, a, b, N)
    assert result == pytest.approx(0.5)

@ pytest.mark.parametrize("N, expected", [
    (1, 0.5),      # h = 1, soma = (0+1)/2 = 0.5, resultado = 0.5
    (2, 0.375),    # h = 0.5, soma = 0.5*(0+1) + f(0.5) = 0.75, resultado = 0.5*0.75
    (4, 0.34375),  # h = 0.25, soma = 0.5*(0+1) + f(0.25)+f(0.5)+f(0.75) = 1.375, resultado = 0.25*1.375
])
def test_trapezio_composto_quadratic_approx(N, expected):
    """
    Para f(x)=x**2 no intervalo [0,1], com N dado, compara contra valor calculado manualmente.
    """
    f = lambda x: x**2
    a, b = 0.0, 1.0
    result = _trapezio_composto(f, a, b, N)
    assert result == pytest.approx(expected)
