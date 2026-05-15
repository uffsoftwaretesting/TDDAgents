import pytest
import math
from src.solve import solve


def test_trapezio_basico():
    # Para f(x)=x no intervalo [0,1] com n=1, a aproximação deve ser (f(0)+f(1))*(1-0)/2 = 0.5
    f = lambda x: x
    resultado = solve(f, 0, 1, 1)
    assert pytest.approx(resultado, rel=1e-9) == 0.5


def test_f_not_callable_raises_type_error():
    # f deve ser callable
    with pytest.raises(TypeError) as excinfo:
        solve(123, 0, 1, 1)
    assert str(excinfo.value) == "f must be a callable taking one float argument"


def test_a_not_numeric_raises_type_error():
    # a e b devem ser int ou float (a não numérico)
    with pytest.raises(TypeError) as excinfo:
        solve(lambda x: x, '0', 1, 1)
    assert str(excinfo.value) == "a and b must be int or float"


def test_b_not_numeric_raises_type_error():
    # a e b devem ser int ou float (b não numérico)
    with pytest.raises(TypeError) as excinfo:
        solve(lambda x: x, 0, '1', 1)
    assert str(excinfo.value) == "a and b must be int or float"


def test_n_not_int_raises_value_error():
    # n deve ser int >= 1 (tipo incorreto)
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 0, 1, 1.5)
    assert str(excinfo.value) == "n must be an integer ≥ 1"


def test_n_less_than_one_raises_value_error():
    # n deve ser >= 1 (valor inválido)
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 0, 1, 0)
    assert str(excinfo.value) == "n must be an integer ≥ 1"


def test_a_greater_or_equal_b_raises_value_error():
    # a deve ser menor que b (caso a == b)
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 1, 1, 1)
    assert str(excinfo.value) == "a must be less than b"

    # a deve ser menor que b (caso a > b)
    with pytest.raises(ValueError) as excinfo2:
        solve(lambda x: x, 2, 1, 1)
    assert str(excinfo2.value) == "a must be less than b"


def test_f_raises_at_lower_limit_propagates():
    # f lança exceção no ponto a; deve ser propagada
    def f(x):
        raise RuntimeError("error at lower limit")

    with pytest.raises(RuntimeError) as excinfo:
        solve(f, 0, 1, 1)
    assert str(excinfo.value) == "error at lower limit"


def test_f_raises_at_upper_limit_propagates():
    # f lança exceção no ponto b; deve ser propagada depois de avaliar a
    def f(x):
        if x == 1:
            raise ValueError("error at upper limit")
        return x

    with pytest.raises(ValueError) as excinfo:
        solve(f, 0, 1, 1)
    assert str(excinfo.value) == "error at upper limit"


def test_f_raises_at_interior_point_propagates():
    # f lança exceção em ponto interno; deve ser propagada
    def f(x):
        if x not in (0, 2):
            raise KeyError("error at interior point")
        return x

    # intervalo [0,2] com n=2 gera ponto interno x=1
    with pytest.raises(KeyError) as excinfo:
        solve(f, 0, 2, 2)
    assert str(excinfo.value) == "'error at interior point'"

# Tests para n=1 (caso mínimo) com funções simples

def test_trapezio_constant_positive():
    # Para f(x)=5.0 no intervalo [2.0,5.0], n=1
    f = lambda x: 5.0
    resultado = solve(f, 2.0, 5.0, 1)
    expected = (f(2.0) + f(5.0)) * (5.0 - 2.0) / 2
    assert pytest.approx(resultado, rel=1e-9) == expected


def test_trapezio_constant_negative():
    # Para f(x)=-3.0 no intervalo [-1.0,1.0], n=1
    f = lambda x: -3.0
    resultado = solve(f, -1.0, 1.0, 1)
    expected = (f(-1.0) + f(1.0)) * (1.0 - (-1.0)) / 2
    assert pytest.approx(resultado, rel=1e-9) == expected

# Novos testes para validar o caso mínimo n=1 com funções não-triviais

def test_trapezio_quadratic():
    # Para f(x)=x**2 no intervalo [0,3], n=1 deve retornar (0+9)*(3)/2 = 13.5
    f = lambda x: x**2
    resultado = solve(f, 0, 3, 1)
    expected = (f(0) + f(3)) * (3 - 0) / 2
    assert pytest.approx(resultado, rel=1e-9) == expected


def test_trapezio_sine():
    # Para f(x)=sin(x) no intervalo [0,pi], n=1 deve retornar (0+0)*(pi)/2 = 0
    f = math.sin
    resultado = solve(f, 0, math.pi, 1)
    expected = (f(0) + f(math.pi)) * (math.pi - 0) / 2
    assert pytest.approx(resultado, rel=1e-9) == expected


def test_trapezio_exp():
    # Para f(x)=exp(x) no intervalo [1,2], n=1
    f = math.exp
    resultado = solve(f, 1, 2, 1)
    expected = (f(1) + f(2)) * (2 - 1) / 2
    assert pytest.approx(resultado, rel=1e-9) == expected

# Novos testes de propagação de exceções adicionais

def test_f_zero_division_at_interior_propagates():
    # f lança ZeroDivisionError em ponto interno
    def f(x):
        return 1/(x - 2)

    # intervalo [1,3] com n=2 gera ponto interno x=2
    with pytest.raises(ZeroDivisionError) as excinfo:
        solve(f, 1, 3, 2)
    assert "division by zero" in str(excinfo.value)


def test_f_custom_exception_at_interior_propagates():
    # f lança exceção customizada em ponto interno
    class CustomError(Exception):
        pass

    def f(x):
        if x in (0, 4):
            return x
        raise CustomError("interior custom")

    # intervalo [0,4] com n=2 gera ponto interno x=2
    with pytest.raises(CustomError) as excinfo:
        solve(f, 0, 4, 2)
    assert str(excinfo.value) == "interior custom"