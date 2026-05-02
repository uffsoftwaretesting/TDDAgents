import pytest
import math
from src.integracao import integracao_simpson_1_3


def test_integracao_simpson_1_3_a_equals_b_returns_zero():
    """
    Verifica que a função retorna 0.0 quando limites são iguais.
    """
    result = integracao_simpson_1_3(lambda x: x**2, 3.14, 3.14, 2)
    assert result == 0.0


def test_integracao_simpson_1_3_f_not_callable_raises_type_error():
    """
    f deve ser callable, caso contrário TypeError.
    """
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(123, 0.0, 1.0, 2)
    msg = str(excinfo.value)
    assert "f" in msg and "callable" in msg

@pytest.mark.parametrize("a", ["foo", None])
def test_integracao_simpson_1_3_non_numeric_a_raises_type_error(a):
    """
    a deve ser numérico, caso contrário TypeError.
    """
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, a, 1.0, 2)
    msg = str(excinfo.value)
    assert "a" in msg and "numérico" in msg

@pytest.mark.parametrize("b", ["bar", None])
def test_integracao_simpson_1_3_non_numeric_b_raises_type_error(b):
    """
    b deve ser numérico, caso contrário TypeError.
    """
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0.0, b, 2)
    msg = str(excinfo.value)
    assert "b" in msg and "numérico" in msg

@pytest.mark.parametrize("N", [0, -2, 2.5])
def test_integracao_simpson_1_3_invalid_N_non_positive_or_non_integer_raises_value_error(N):
    """
    N deve ser inteiro par e maior que zero, caso contrário ValueError.
    """
    with pytest.raises(ValueError, match="N deve ser inteiro par e maior que zero."):
        integracao_simpson_1_3(lambda x: x, 0.0, 1.0, N)


def test_integracao_simpson_1_3_N_odd_raises_value_error():
    """
    N inteiro mas ímpar deve levantar ValueError específico.
    """
    with pytest.raises(ValueError, match="N deve ser par."):
        integracao_simpson_1_3(lambda x: x, 0.0, 1.0, 3)

# Testes de integração para funções básicas
declare_test_data = [
    (lambda x: 1, 0.0, 10.0, 10, 10.0),  # integral de 1 dx = b - a = 10.0
    (lambda x: x, 0.0, 2.0, 10, (2.0**2 - 0.0**2)/2),  # integral de x dx = (b^2 - a^2)/2
    (lambda x: x**2, -1.0, 3.0, 10, (3.0**3 - (-1.0)**3)/3),  # integral de x^2 dx = (b^3 - a^3)/3
]

@pytest.mark.parametrize("f,a,b,N,expected", declare_test_data)
def test_integracao_simpson_1_3_basic_functions(f, a, b, N, expected):
    """
    Verifica aproximação de Simpson para funções constantes, lineares e quadráticas comparando com a solução exata.
    """
    result = integracao_simpson_1_3(f, a, b, N)
    assert result == pytest.approx(expected, rel=1e-6)


def test_integracao_simpson_1_3_deterministic_for_quadratic():
    """
    Verifica que chamadas repetidas retornam exatamente o mesmo valor (determinismo).
    """
    f = lambda x: x**2
    a, b, N = 0.5, 2.5, 10
    r1 = integracao_simpson_1_3(f, a, b, N)
    r2 = integracao_simpson_1_3(f, a, b, N)
    assert r1 == r2

# Novos testes para garantir propagação de exceções de f
class CustomException(Exception):
    pass

def test_integracao_simpson_1_3_propagates_exception_when_f_raises_at_endpoint_a():
    """
    Verifica que a exceção de f em x == a é propagada.
    """
    def f(x):
        if x == 0.0:
            raise CustomException("error at a")
        return x
    with pytest.raises(CustomException):
        integracao_simpson_1_3(f, 0.0, 1.0, 2)

def test_integracao_simpson_1_3_propagates_exception_when_f_raises_at_endpoint_b():
    """
    Verifica que a exceção de f em x == b é propagada.
    """
    def f(x):
        if x == 1.0:
            raise CustomException("error at b")
        return x
    with pytest.raises(CustomException):
        integracao_simpson_1_3(f, 0.0, 1.0, 2)

def test_integracao_simpson_1_3_propagates_exception_when_f_raises_at_interior_point():
    """
    Verifica que a exceção de f em ponto interior (x == 0.5) é propagada.
    """
    def f(x):
        if x == 0.5:
            raise CustomException("domain error")
        return x
    with pytest.raises(CustomException):
        integracao_simpson_1_3(f, 0.0, 1.0, 2)

# Fase 5: Precisão e determinismo completo para função matemética não-polomial
@pytest.mark.parametrize("repeats", [2, 5, 10])
def test_integracao_simpson_1_3_deterministic_for_sine_multiple_calls(repeats):
    """
    Verifica que múltiplas chamadas consecutivas com sin(x) retornam exatamente o mesmo float.
    """
    f = math.sin
    a, b, N = 0.0, math.pi, 100
    results = [integracao_simpson_1_3(f, a, b, N) for _ in range(repeats)]
    # all results must be bitwise-equal floats
    first = results[0]
    for r in results[1:]:
        assert r == first