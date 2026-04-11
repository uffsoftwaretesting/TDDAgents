import pytest
import inspect
from integrador_trapezio import solve
import math


def test_solve_is_callable():
    # solve deve existir e ser chamável
    assert callable(solve)


def test_zero_interval_returns_zero():
    # Para intervalo nulo, deve retornar 0.0
    result = solve(lambda x: x, 2.0, 2.0, 5)
    assert isinstance(result, float)
    assert result == 0.0


def test_invalid_n_raises_value_error():
    # n <= 0 deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 0.0, 1.0, 0)
    assert "integer" in str(excinfo.value).lower()


def test_n_not_integer_raises_value_error():
    # n não inteiro deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 0.0, 1.0, 2.5)
    assert "integer" in str(excinfo.value).lower()


def test_b_less_than_a_raises_value_error():
    # b < a deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 1.0, 0.0, 10)
    assert "greater than or equal to a" in str(excinfo.value).lower()


def test_non_numeric_limits_raise_value_error_a_not_numeric():
    # a não numérico deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, "a", 1.0, 1)
    assert "numeric" in str(excinfo.value).lower()


def test_non_numeric_limits_raise_value_error_b_not_numeric():
    # b não numérico deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(lambda x: x, 0.0, "b", 1)
    assert "numeric" in str(excinfo.value).lower()


def test_f_not_callable_raises_value_error():
    # f não callable deve disparar ValueError
    with pytest.raises(ValueError) as excinfo:
        solve(123, 0.0, 1.0, 1)
    assert "callable" in str(excinfo.value).lower()


def test_signature_parameters_order():
    # A assinatura deve conter exatamente os parâmetros f, a, b, n na ordem
    sig = inspect.signature(solve)
    assert list(sig.parameters.keys()) == ['f', 'a', 'b', 'n']


def test_nonzero_interval_returns_float_with_dummy_function():
    # Chamando com função dummy que retorna zero, deve retornar float mesmo em intervalo não-nulo
    dummy = lambda x: 0.0
    result = solve(dummy, 0.0, 1.0, 1)
    assert isinstance(result, float)
    assert result == 0.0


def test_zero_interval_does_not_invoke_function():
    # Para intervalo nulo, solve deve retornar 0.0 sem chamar f
    def f(x):
        raise RuntimeError("Function should not be called for zero interval")
    result = solve(f, 3.5, 3.5, 10)
    assert result == 0.0


def test_constant_function_n_one():
    # Para f(x)=const e n=1, integral = const * (b-a)
    c = 2.5
    a = 1.0
    b = 4.0
    result = solve(lambda x: c, a, b, 1)
    expected = c * (b - a)
    assert isinstance(result, float)
    assert result == expected


def test_linear_function_n_one():
    # Para f(x)=x e n=1, trapézio = (b-a)/2 * (f(a) + f(b))
    a = 0.0
    b = 2.0
    result = solve(lambda x: x, a, b, 1)
    expected = (b - a) / 2 * (a + b)
    assert isinstance(result, float)
    assert result == pytest.approx(expected)


def test_linear_function_n_greater_than_one():
    # Para f(x)=x e n>1, método composto deve ser exato para função linear
    a = -1.0
    b = 2.0
    # Valor analítico da integral de x de a até b é (b^2 - a^2)/2
    expected = (b*b - a*a) / 2
    for n in [2, 3, 5, 10]:
        result = solve(lambda x: x, a, b, n)
        assert isinstance(result, float)
        assert result == pytest.approx(expected)

# Testes de comparação com valor analítico para polinomiais e trigonométricas
@pytest.mark.parametrize("f, a, b, n, analytic, tol", [
    (lambda x: x**2, 0.0, 1.0, 10, 1/3, 2e-3),
    (lambda x: x**2, 0.0, 1.0, 100, 1/3, 2e-5),
    (lambda x: x**2, 0.0, 1.0, 1000, 1/3, 2e-7),
    (math.sin, 0.0, math.pi, 10, 2.0, 3e-2),
    (math.sin, 0.0, math.pi, 100, 2.0, 3e-4),
    (math.cos, 0.0, math.pi/2, 10, 1.0, 3e-2),
    (math.cos, 0.0, math.pi/2, 100, 1.0, 3e-4),
])

def test_composite_trapezoid_against_analytic(f, a, b, n, analytic, tol):
    """
    Verifica aproximação da integral de funções polinomiais e trigonométricas
    contra o valor analítico, aceitando erro absoluto dentro de tol.
    """
    result = solve(f, a, b, n)
    assert isinstance(result, float)
    assert result == pytest.approx(analytic, abs=tol)

# Novos testes de documentação
def test_solve_has_docstring():
    """
    Verifica que a função solve possua docstring não vazia.
    """
    doc = solve.__doc__
    assert isinstance(doc, str) and doc.strip(), "A função solve deve ter uma docstring não vazia."


def test_docstring_contains_parameters_and_return_sections():
    """
    Verifica que a docstring contenha seções 'Parâmetros' e 'Retorna' e que mencione cada parâmetro.
    """
    doc = solve.__doc__.lower()
    assert 'parâmetros' in doc, "Docstring deve conter seção 'Parâmetros'"
    for param in ['f', 'a', 'b', 'n']:
        assert f"{param} (" in doc or f"{param} :" in doc or f"{param} " in doc, f"Docstring deve mencionar o parâmetro '{param}'"
    assert 'retorna' in doc, "Docstring deve conter seção 'Retorna'"


def test_docstring_mentions_composite_trapezoidal_rule():
    """
    Verifica que a docstring mencione a regra composta do trapézio.
    """
    doc = solve.__doc__.lower()
    assert 'regra composta do trapézio' in doc, "Docstring deve mencionar 'regra composta do trapézio'"