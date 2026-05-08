import pytest
from src.integration import integracao_simpson_1_3


def test_integracao_polinomial():
    # Teste exato para f(x)=x**3 em [0,1] com N=2 (integral = 1/4)
    assert integracao_simpson_1_3(lambda x: x**3, 0, 1, 2) == pytest.approx(0.25)


def test_integracao_f_not_callable():
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(5, 0, 1, 2)
    assert "f deve ser uma função callable" in str(excinfo.value)


def test_integracao_a_b_not_numeric():
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 'a', 1, 2)
    assert "a e b devem ser valores numéricos" in str(excinfo.value)


def test_integracao_b_not_numeric():
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 'b', 2)
    assert "a e b devem ser valores numéricos" in str(excinfo.value)


def test_integracao_N_not_int():
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, 2.5)
    assert "N deve ser um inteiro par e positivo" in str(excinfo.value)

@pytest.mark.parametrize("N", [0, -2, -3])
def test_integracao_N_non_positive(N):
    # N <= 0 (zero ou negativo, par ou ímpar) deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, N)
    assert "N deve ser um inteiro par e maior que zero" in str(excinfo.value)


def test_integracao_N_impar():
    with pytest.raises(ValueError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, 3)
    assert "N deve ser um inteiro par e maior que zero" in str(excinfo.value)


def test_integracao_a_greater_b():
    # Integral de f(x)=x sobre [1,0] com N=2 deve ser -0.5
    result = integracao_simpson_1_3(lambda x: x, 1, 0, 2)
    assert result == pytest.approx(-0.5)


def test_integracao_polinomial_variado():
    # Teste para f(x)=2*x^2 + 3*x + 1 em [0,1] com N par
    f = lambda x: 2*x**2 + 3*x + 1
    # Integral exata: 2/3 + 3/2 + 1 = 0.6666667 + 1.5 + 1 = 3.1666667
    expected = 2/3 + 3/2 + 1
    assert integracao_simpson_1_3(f, 0, 1, 4) == pytest.approx(expected)

# Novos testes para validações de tipo e conversão
class Functor:
    """
    Classe com __call__, para testar aceitação de objetos callable
    """
    def __call__(self, x):
        return x


def test_integracao_callable_class():
    f = Functor()
    result = integracao_simpson_1_3(f, 0, 1, 2)
    # ∫₀¹ x dx = 0.5
    assert result == pytest.approx(0.5)

@pytest.mark.parametrize("bad_input", (["a"], None, {}))
def test_integracao_a_b_invalid_types(bad_input):
    # a inválido
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, bad_input, 1, 2)
    assert "a e b devem ser valores numéricos" in str(excinfo.value)
    # b inválido
    with pytest.raises(TypeError) as excinfo2:
        integracao_simpson_1_3(lambda x: x, 0, bad_input, 2)
    assert "a e b devem ser valores numéricos" in str(excinfo2.value)

@pytest.mark.parametrize("N", ("4", None, 2.0))
def test_integracao_N_not_int_various(N):
    with pytest.raises(TypeError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, N)
    assert "N deve ser um inteiro par e positivo" in str(excinfo.value)


def test_integracao_result_type_float_for_int_bounds():
    # Testa conversão de a,b int para float e resultado do tipo float
    result = integracao_simpson_1_3(lambda x: x, 0, 2, 4)
    assert isinstance(result, float)
    # ∫₀² x dx = 2
    assert result == pytest.approx(2.0)

# NOVOS TESTES PARA O SUB-REQUISITO 3
@pytest.mark.parametrize("N", [1, 5])
def test_integracao_N_odd_positive_raises(N):
    # Para N ímpar e positivo deve lançar ValueError
    with pytest.raises(ValueError) as excinfo:
        integracao_simpson_1_3(lambda x: x, 0, 1, N)
    assert "N deve ser um inteiro par e maior que zero" in str(excinfo.value)

@pytest.mark.parametrize("N", [2, 4, 6])
def test_integracao_even_N_no_error(N):
    # Para N par e maior que zero não deve lançar e aproxime ∫₀¹ x^2 dx = 1/3
    result = integracao_simpson_1_3(lambda x: x**2, 0, 1, N)
    assert result == pytest.approx(1/3)

# Testes adicionais para validação de a > b (h negativo) em várias funções
@pytest.mark.parametrize("func,a,b,N,expected", [
    (lambda x: x**2, 1, 0, 2, -1/3),
    (lambda x: x**3, 1, 0, 2, -1/4),
    (lambda x: 5,     1, 0, 2, -5.0),
])
def test_integracao_a_greater_b_various_functions(func, a, b, N, expected):
    result = integracao_simpson_1_3(func, a, b, N)
    assert result == pytest.approx(expected)
