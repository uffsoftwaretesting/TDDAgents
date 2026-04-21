import pytest
import math
from mathutils.derivada_diferenca_central import derivada_diferenca_central

def test_derivada_constante_zero():
    # Função constante f(x) = 5.0 deve ter derivada zero em qualquer ponto
    f = lambda x: 5.0
    x0 = 1.234
    h = 1e-3
    result = derivada_diferenca_central(f, x0, h)
    # Aproximação deve ser próxima a zero
    assert pytest.approx(result, abs=1e-8) == 0.0

def test_f_not_callable_raises_type_error():
    # f não é callable
    with pytest.raises(TypeError) as excinfo:
        derivada_diferenca_central(123, 0.0, 1e-3)
    assert str(excinfo.value) == "f deve ser Callable[[float], float]"

def test_x_not_float_raises_type_error():
    # x não é float
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        derivada_diferenca_central(f, "0.0", 1e-3)
    assert str(excinfo.value) == "x deve ser float"

def test_x_as_int_raises_type_error():
    # x é int, não float
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        derivada_diferenca_central(f, 1, 1e-3)
    assert str(excinfo.value) == "x deve ser float"

def test_h_not_float_raises_type_error():
    # h não é float
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        derivada_diferenca_central(f, 0.0, "1e-3")
    assert str(excinfo.value) == "h deve ser float"

def test_h_as_int_raises_type_error():
    # h é int, não float
    f = lambda x: x
    with pytest.raises(TypeError) as excinfo:
        derivada_diferenca_central(f, 1.0, 1)
    assert str(excinfo.value) == "h deve ser float"

def test_h_zero_raises_value_error():
    # h igual a zero
    f = lambda x: x
    with pytest.raises(ValueError) as excinfo:
        derivada_diferenca_central(f, 0.0, 0.0)
    assert str(excinfo.value) == "h deve ser maior que zero"

def test_h_negative_raises_value_error():
    # h negativo
    f = lambda x: x
    with pytest.raises(ValueError) as excinfo:
        derivada_diferenca_central(f, 0.0, -1e-3)
    assert str(excinfo.value) == "h deve ser maior que zero"

def test_propagate_exception_from_f_at_x_plus_h():
    # Se f lançar ZeroDivisionError em x+h, deve propagar sem captura
    x0 = 3.0
    h = 0.1
    def f(x):
        if math.isclose(x, x0 + h, rel_tol=0, abs_tol=1e-12):
            raise ZeroDivisionError("error plus")
        return 0.0
    with pytest.raises(ZeroDivisionError) as excinfo:
        derivada_diferenca_central(f, x0, h)
    assert str(excinfo.value) == "error plus"

def test_propagate_exception_from_f_at_x_minus_h():
    # Se f lançar ValueError em x-h, deve propagar sem captura
    x0 = 3.0
    h = 0.1
    def f(x):
        if math.isclose(x, x0 - h, rel_tol=0, abs_tol=1e-12):
            raise ValueError("error minus")
        return 0.0
    with pytest.raises(ValueError) as excinfo:
        derivada_diferenca_central(f, x0, h)
    assert str(excinfo.value) == "error minus"

@pytest.mark.parametrize("h", [1e-1, 1e-2, 1e-4, 1e-6, 1e-8])
def test_constante_erro_ordem_quadratica(h):
    # Para f constante, o erro da derivada deve ser O(h^2): |D_h f| <= h^2
    C = 3.1415
    f = lambda x: C
    x0 = 2.0
    result = derivada_diferenca_central(f, x0, h)
    assert abs(result) <= h * h

@pytest.mark.parametrize("a,b,x0,h", [
    (2.0, -3.0, 1.5, 1e-3),
    (-1.0, 4.5, -2.0, 1e-5),
])
def test_linear_function_derivative(a, b, x0, h):
    # f(x) = a*x + b deve retornar derivada a
    f = lambda x: a * x + b
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(a, rel=1e-8)

@pytest.mark.parametrize("a,b,c,x0,h", [
    (1.5, -2.0, 0.5, 0.0, 1e-3),
    (0.0, 3.0, -1.0, 2.5, 1e-6),
])
def test_quadratic_function_derivative(a, b, c, x0, h):
    # f(x) = a*x^2 + b*x + c deve retornar derivada 2*a*x0 + b
    f = lambda x: a * x * x + b * x + c
    expected = 2 * a * x0 + b
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(expected, rel=1e-8)

@pytest.mark.parametrize("x0,h", [
    (math.pi/6, 1e-4),
    (math.pi/4, 1e-6),
])
def test_trig_sin_derivative(x0, h):
    # f(x) = sin(x) deve retornar cos(x)
    f = math.sin
    expected = math.cos(x0)
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(expected, rel=1e-8)

@pytest.mark.parametrize("x0,h", [
    (math.pi/6, 1e-4),
    (math.pi/4, 1e-6),
])
def test_trig_cos_derivative(x0, h):
    # f(x) = cos(x) deve retornar -sin(x)
    f = math.cos
    expected = -math.sin(x0)
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(expected, rel=1e-8)

# Novos testes para validar O(h^2) em funções lineares
@pytest.mark.parametrize("a,b,x0", [
    (2.0, -3.0, 1.5),
    (-1.0, 4.5, -2.0),
    (0.0, 0.0, 3.3),
])
@pytest.mark.parametrize("h", [1e-1, 1e-2, 1e-3, 1e-4, 1e-5])
def test_linear_erro_ordem_quadratica(a, b, x0, h):
    # Para f(x)=a*x+b, erro da derivada deve ser O(h^2): |D_h f - a| <= |a| * h^2
    f = lambda x: a * x + b
    expected = a
    result = derivada_diferenca_central(f, x0, h)
    assert abs(result - expected) <= abs(a) * h * h

# Novos testes para validar O(h^2) em funções quadráticas
@pytest.mark.parametrize("a,b,c,x0", [
    (1.5, -2.0, 0.5, 0.0),
    (0.0, 3.0, -1.0, 2.5),
    (-2.0, 1.0, 1.0, -1.2),
])
@pytest.mark.parametrize("h", [1e-1, 1e-2, 1e-3, 1e-4, 1e-5])
def test_quadratic_erro_ordem_quadratica(a, b, c, x0, h):
    # Para f(x)=a*x^2+b*x+c, erro da derivada deve ser O(h^2): |D_h f - (2a*x0+b)| <= |2a*x0+b| * h^2
    f = lambda x: a * x * x + b * x + c
    expected = 2 * a * x0 + b
    result = derivada_diferenca_central(f, x0, h)
    assert abs(result - expected) <= abs(expected) * h * h

# Novos testes de trigonometria em pontos-chave completos
@pytest.mark.parametrize("x0,h", [
    (0.0, 1e-6),
    (math.pi/2, 1e-6),
    (math.pi, 1e-6),
    (3*math.pi/2, 1e-6),
    (2*math.pi, 1e-6),
])
def test_trig_sin_derivative_key_points(x0, h):
    # f(x) = sin(x) → derivada exata = cos(x)
    f = math.sin
    expected = math.cos(x0)
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(expected, rel=1e-8)

@pytest.mark.parametrize("x0,h", [
    (0.0, 1e-6),
    (math.pi/2, 1e-6),
    (math.pi, 1e-6),
    (3*math.pi/2, 1e-6),
    (2*math.pi, 1e-6),
])
def test_trig_cos_derivative_key_points(x0, h):
    # f(x) = cos(x) → derivada exata = -sin(x)
    f = math.cos
    expected = -math.sin(x0)
    result = derivada_diferenca_central(f, x0, h)
    assert result == pytest.approx(expected, rel=1e-8)

# Novo teste de propagação de ZeroDivisionError em x-h
def test_propagate_zero_division_error_from_f_at_x_minus_h():
    x0 = 2.0
    h = 0.5
    def f(x):
        if math.isclose(x, x0 - h, rel_tol=0, abs_tol=1e-12):
            raise ZeroDivisionError("minus zero div")
        return x
    with pytest.raises(ZeroDivisionError) as excinfo:
        derivada_diferenca_central(f, x0, h)
    assert str(excinfo.value) == "minus zero div"