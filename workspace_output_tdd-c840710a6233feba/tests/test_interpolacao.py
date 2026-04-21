import pytest
from interpolacao_lagrange import interpolacao_lagrange


def test_single_node_returns_fx():
    # Com apenas um nó, deve retornar f(x_nos[0]) mesmo que x_alvo seja diferente
    def f_func(x):
        return x ** 2

    result = interpolacao_lagrange([1.0], f_func, 2.0)
    assert result == 1.0


@pytest.mark.parametrize(
    "x_nos, f_func, x_alvo",
    [
        ([], lambda x: x, 0.0),
        ([1.0, 1.0], lambda x: x, 1.0),
    ],
)
def test_invalid_nodes_raise_value_error(x_nos, f_func, x_alvo):
    with pytest.raises(ValueError):
        interpolacao_lagrange(x_nos, f_func, x_alvo)


@pytest.mark.parametrize(
    "x_nos, f_func, x_alvo",
    [
        ([1, 2.0], lambda x: x, 1.0),
        ([1.0, 2.0], lambda x: "a", 1.0),
        ([1.0, 2.0], lambda x: x, "a"),
    ],
)
def test_invalid_types_raise_type_error(x_nos, f_func, x_alvo):
    with pytest.raises(TypeError):
        interpolacao_lagrange(x_nos, f_func, x_alvo)


def test_linear_interpolation_two_points_for_square_function():
    # Para f(x)=x^2 em dois nós, espera-se interpolação linear
    # entre (1,1) e (2,4)
    x_nos = [1.0, 2.0]

    def f_func(x):
        return x ** 2

    x_alvo = 1.5
    result = interpolacao_lagrange(x_nos, f_func, x_alvo)
    assert pytest.approx(result, rel=1e-12) == 2.5
