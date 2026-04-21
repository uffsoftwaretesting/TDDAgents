import pytest
from interpolacao_lagrange import interpolacao_lagrange


def test_x_nos_generator_raises_value_error():
    # Um generator não possui __len__, deve gerar ValueError inicial
    gen = (i for i in [1.0, 2.0])

    def f_func(x):
        return float(x)

    with pytest.raises(ValueError) as excinfo:
        interpolacao_lagrange(gen, f_func, 1.5)
    assert str(excinfo.value) == "É necessário pelo menos um nó de abscissa"


def test_x_nos_tuple_valid_interpolation():
    # Sequência como tuple deve funcionar corretamente
    x_nos = (0.0, 2.0, 4.0)

    def f(x):
        return x * 2.0

    x_alvo = 3.0
    result = interpolacao_lagrange(x_nos, f, x_alvo)
    assert pytest.approx(result, rel=1e-12) == 6.0


def test_non_callable_f_raises_type_error():
    # f não chamável deve gerar TypeError ao tentativa de chamada
    x_nos = [1.0]
    f = 123  # não é callable

    with pytest.raises(TypeError):
        interpolacao_lagrange(x_nos, f, 1.0)


def test_extrapolation_quadratic_above_range():
    # Testa extrapolação acima do range para f(x)=x**2
    x_nos = [0.0, 1.0, 2.0]

    def f(x):
        return x ** 2

    x_alvo = 4.0
    result = interpolacao_lagrange(x_nos, f, x_alvo)
    assert pytest.approx(result, rel=1e-12) == 16.0
